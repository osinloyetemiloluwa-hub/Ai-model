"""``corvin-maintainer`` — operator CLI for the ADR-0178 Tier-CONTRIBUTOR loop.

Turns the "production-remaining" steps into commands:

  corvin-maintainer keygen [--out-priv FILE]      # one-time: Ed25519 keypair
  corvin-maintainer issue  --priv-file F --instance ID [--subject S] [--ttl-days N]
  corvin-maintainer verify                         # checks the env config
  corvin-maintainer run --repo DIR --patch P.json [--diagnosis D.json]
                        [--test-cmd "..."] [--direct-main] [--push]

The PRIVATE key is YOUR secret — keygen writes it to a 0600 file (or stdout) and
it never leaves your machine. ``issue`` mints a capability bound to one instance.
``run`` drives the L6 loop with a built-in pytest gate_runner and a file-based
patch_source (the maintainer/agent authors the patch JSON). Deny-by-default
everywhere — without a valid pinned key + token, ``run`` refuses.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from . import maintainer_capability as MC
from . import maintenance_loop as ML


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _read_priv(args) -> bytes:
    if getattr(args, "priv_file", None):
        raw = Path(args.priv_file).read_text(encoding="utf-8").strip()
    elif os.environ.get("CORVIN_MAINTAINER_PRIV"):
        raw = os.environ["CORVIN_MAINTAINER_PRIV"].strip()
    else:
        raise SystemExit("error: provide --priv-file or set CORVIN_MAINTAINER_PRIV")
    return base64.b64decode(raw)


def cmd_keygen(args) -> int:
    from cryptography.hazmat.primitives import serialization as S
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw, S.NoEncryption())
    pub = sk.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
    pub_b64 = _b64(pub)
    if args.out_priv:
        p = Path(args.out_priv)
        # write 0600 BEFORE content so it is never briefly world-readable
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(_b64(priv) + "\n")
        print(f"private key → {p} (mode 0600, KEEP SECRET, never commit)")
    else:
        print("PRIVATE KEY (store offline + secret, never commit):")
        print("  " + _b64(priv))
    print("\nPUBLIC KEY — pin it on the maintainer install:")
    print(f"  export CORVIN_MAINTAINER_PUBKEY={pub_b64}")
    return 0


def cmd_issue(args) -> int:
    priv = _read_priv(args)
    iid = args.instance or MC.current_instance_id()
    if not iid:
        raise SystemExit("error: no --instance and current_instance_id() is empty "
                         "(set CORVIN_INSTANCE_ID or pass --instance)")
    tok = MC.issue(priv, instance_id=iid, subject=args.subject,
                   ttl_seconds=int(args.ttl_days) * 86400)
    print("capability token (set as CORVIN_MAINTAINER_CAP on instance "
          f"'{iid}'):\n")
    print(tok)
    return 0


def cmd_verify(args) -> int:
    v = MC.is_contributor()
    print(json.dumps({
        "allowed": v.allowed, "reason": v.reason, "subject": v.subject,
        "instance_id": MC.current_instance_id() or "(empty)",
        "pubkey_pinned": bool(os.environ.get("CORVIN_MAINTAINER_PUBKEY")),
        "token_present": bool(os.environ.get("CORVIN_MAINTAINER_CAP")),
    }, indent=2))
    return 0 if v.allowed else 1


def _file_patch_source(patch_path: str):
    def src(diag: dict):
        spec = json.loads(Path(patch_path).read_text(encoding="utf-8"))
        edits = [ML.PatchEdit(e["path"], e["new_content"]) for e in spec.get("edits", [])]
        if not edits:
            return None
        return ML.Patch(
            diagnosis_id=str(spec.get("diagnosis_id") or diag.get("id") or "diag"),
            summary=spec.get("summary", "maintenance fix"),
            risk_class=spec.get("risk_class", "refactor"),
            edits=edits,
        )
    return src


def _pytest_gate(test_cmd: list[str], repo: str):
    def g() -> tuple[bool, str]:
        p = subprocess.run(test_cmd, cwd=repo, capture_output=True, text=True, timeout=1800)
        return (p.returncode == 0, (p.stdout + p.stderr)[-1200:])
    return g


def cmd_run(args) -> int:
    diag = {"id": "diag"}
    if args.diagnosis:
        diag = json.loads(Path(args.diagnosis).read_text(encoding="utf-8"))
    test_cmd = (args.test_cmd.split() if args.test_cmd
                else [sys.executable, "-m", "pytest", "core/console/tests", "-q"])
    r = ML.run_maintenance_loop(
        diagnosis=diag,
        repo_dir=args.repo,
        patch_source=_file_patch_source(args.patch),
        capability_token=None,                      # from CORVIN_MAINTAINER_CAP env
        gate_runner=_pytest_gate(test_cmd, args.repo),
        enable_direct_main=bool(args.direct_main),
        enable_push=bool(args.push),
    )
    print(json.dumps({
        "status": r.status, "branch": r.branch, "commit": r.commit,
        "requires_ack": r.requires_ack, "detail": r.detail,
        "gate_reasons": r.gate_reasons, "telemetry": r.telemetry,
    }, indent=2))
    # exit 0 only on a fully-resolved outcome
    return 0 if r.status in ("merged", "pushed", "pr_ready") else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="corvin-maintainer",
                                description="ADR-0178 Tier-CONTRIBUTOR maintainer CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("keygen", help="generate an Ed25519 maintainer keypair")
    g.add_argument("--out-priv", help="write the private key to this 0600 file")
    g.set_defaults(fn=cmd_keygen)

    i = sub.add_parser("issue", help="mint a maintainer.commit capability token")
    i.add_argument("--priv-file", help="file holding the base64 private key")
    i.add_argument("--instance", help="instance_id to bind (default: current)")
    i.add_argument("--subject", default="shumway")
    i.add_argument("--ttl-days", default="90")
    i.set_defaults(fn=cmd_issue)

    v = sub.add_parser("verify", help="check the pinned key + token in the env")
    v.set_defaults(fn=cmd_verify)

    r = sub.add_parser("run", help="drive one L6 maintenance-loop iteration")
    r.add_argument("--repo", required=True, help="repo working dir to operate on")
    r.add_argument("--patch", required=True, help="patch spec JSON (edits)")
    r.add_argument("--diagnosis", help="diagnosis JSON (optional)")
    r.add_argument("--test-cmd", help="override the gate test command")
    r.add_argument("--direct-main", action="store_true",
                   help="allow ff-merge to main for low-risk+green (default: PR only)")
    r.add_argument("--push", action="store_true", help="push origin main (implies risk)")
    r.set_defaults(fn=cmd_run)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
