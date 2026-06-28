/**
 * Hook to check if a chat has any running tasks.
 * Monitors IndexedDB for task status changes.
 */

import { useEffect, useState } from "react";
import { getTasksByChatKey } from "@/lib/task-db";

export interface ChatTaskStatus {
  hasRunningTasks: boolean;
  taskCount: number;
  status: "idle" | "running" | "pending";
}

export function useChatTaskStatus(chatKey: string): ChatTaskStatus {
  const [status, setStatus] = useState<ChatTaskStatus>({
    hasRunningTasks: false,
    taskCount: 0,
    status: "idle",
  });

  useEffect(() => {
    if (!chatKey) {
      setStatus({
        hasRunningTasks: false,
        taskCount: 0,
        status: "idle",
      });
      return;
    }

    const checkTasks = async () => {
      try {
        const tasks = await getTasksByChatKey(chatKey);

        // Count tasks by status
        const runningTasks = tasks.filter((t) => t.status === "running");
        const pendingTasks = tasks.filter((t) => t.status === "pending");

        setStatus({
          hasRunningTasks: runningTasks.length > 0,
          taskCount: tasks.length,
          status:
            runningTasks.length > 0
              ? "running"
              : pendingTasks.length > 0
              ? "pending"
              : "idle",
        });
      } catch (err) {
        console.warn(`[ChatTaskStatus] Failed to load tasks for ${chatKey}:`, err);
      }
    };

    // Check immediately
    checkTasks();

    // Poll every 2 seconds to catch updates
    const interval = setInterval(checkTasks, 2000);
    return () => clearInterval(interval);
  }, [chatKey]);

  return status;
}
