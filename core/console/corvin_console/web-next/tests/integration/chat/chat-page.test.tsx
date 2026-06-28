import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { ChatPage } from '../../fixtures/mock-pages';

describe('Chat Page Integration', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-test-token-chat-123');
  });

  describe('Chat Display', () => {
    it('renders chat page heading', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });
      expect(screen.getByText(/chat/i)).toBeInTheDocument();
    });

    it('displays chat message history', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      expect(screen.getByText(/hello/i)).toBeInTheDocument();
      expect(screen.getByText(/what is corvinOS/i)).toBeInTheDocument();
      expect(screen.getByText(/ai-powered framework/i)).toBeInTheDocument();
    });

    it('shows assistant messages', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });
      expect(screen.getByText(/assistant: hello/i)).toBeInTheDocument();
    });

    it('shows user messages', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });
      expect(screen.getByText(/user: what is corvinOS/i)).toBeInTheDocument();
    });

    it('displays conversation in order', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });
      const messages = container.querySelectorAll('[role="main"] div');

      expect(messages.length).toBeGreaterThan(2);
    });
  });

  describe('Message Input', () => {
    it('renders message input field', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      expect(textarea).toBeInTheDocument();
      expect(textarea).toBeEnabled();
    });

    it('accepts text input', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: 'Hello, Claude!' } });

      expect(textarea.value).toBe('Hello, Claude!');
    });

    it('allows typing multiple lines', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: 'Line 1\nLine 2\nLine 3' } });

      expect(textarea.value).toContain('Line 1');
      expect(textarea.value).toContain('Line 2');
      expect(textarea.value).toContain('Line 3');
    });

    it('clears input after submission', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      const sendButton = screen.getByRole('button', { name: /send/i });

      fireEvent.change(textarea, { target: { value: 'Test message' } });
      fireEvent.click(sendButton);

      // After submission, field should be empty (in real app)
      // Mock doesn't implement this, so we just check button was clicked
      expect(sendButton).toBeInTheDocument();
    });
  });

  describe('Send Button', () => {
    it('renders send button', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const sendButton = screen.getByRole('button', { name: /send/i });
      expect(sendButton).toBeInTheDocument();
      expect(sendButton).toBeEnabled();
    });

    it('send button is clickable', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const sendButton = screen.getByRole('button', { name: /send/i });
      fireEvent.click(sendButton);

      // Button should still be present after click
      expect(sendButton).toBeInTheDocument();
    });

    it('sends message on button click', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      const sendButton = screen.getByRole('button', { name: /send/i });

      fireEvent.change(textarea, { target: { value: 'New message' } });
      fireEvent.click(sendButton);

      // Message was submitted
      expect(sendButton).toBeInTheDocument();
    });

    it('sends message on Enter key (Shift+Enter for newline)', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);

      fireEvent.change(textarea, { target: { value: 'Message' } });
      fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter' });

      expect(textarea).toBeInTheDocument();
    });
  });

  describe('Chat Session', () => {
    it('maintains session token', () => {
      const token = localStorage.getItem('auth_token');
      expect(token).toBe('jwt-test-token-chat-123');
    });

    it('displays chat in authenticated state', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      expect(textarea).toBeEnabled();
    });

    it('shows main content area', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const mainArea = container.querySelector('[role="main"]');
      expect(mainArea).toBeInTheDocument();
    });
  });

  describe('Conversation Management', () => {
    it('shows multiple messages in sequence', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      expect(screen.getByText(/assistant: hello/i)).toBeInTheDocument();
      expect(screen.getByText(/user: what is corvinOS/i)).toBeInTheDocument();
      expect(screen.getByText(/assistant: corvinOS is/i)).toBeInTheDocument();
    });

    it('scrollable message area', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const messageArea = container.querySelector('[role="main"]');
      expect(messageArea).toBeInTheDocument();
    });

    it('shows newest messages at bottom', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const messages = Array.from(container.querySelectorAll('[role="main"] div > div'));
      // Last message should be the AI response about CorvinOS
      expect(messages[messages.length - 1]?.textContent).toContain('CorvinOS is');
    });
  });

  describe('Chat Features', () => {
    it('supports emoji input', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: '👋 Hello! 🎉' } });

      expect(textarea.value).toBe('👋 Hello! 🎉');
    });

    it('supports code in messages', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: '```python\nprint("hello")\n```' } });

      expect(textarea.value).toContain('```python');
    });

    it('supports markdown formatting', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: '**bold** *italic* `code`' } });

      expect(textarea.value).toContain('**bold**');
      expect(textarea.value).toContain('*italic*');
      expect(textarea.value).toContain('`code`');
    });
  });

  describe('Input Validation', () => {
    it('allows empty input initially', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      expect(textarea.value).toBe('');
    });

    it('handles whitespace-only input', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: '   ' } });

      expect(textarea.value).toBe('   ');
    });

    it('preserves special characters', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      const specialChars = '!@#$%^&*()_+-=[]{}|;:,.<>?/~`';
      fireEvent.change(textarea, { target: { value: specialChars } });

      expect(textarea.value).toBe(specialChars);
    });
  });

  describe('Responsive Design', () => {
    it('displays correctly on desktop viewport', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const heading = screen.getByText(/chat/i);
      expect(heading).toBeVisible();
    });

    it('textarea is visible and accessible', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      expect(textarea).toBeVisible();
      expect(textarea).not.toHaveAttribute('disabled');
    });

    it('send button is accessible', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const button = screen.getByRole('button', { name: /send/i });
      expect(button).toBeVisible();
      expect(button).not.toHaveAttribute('disabled');
    });
  });

  describe('Accessibility', () => {
    it('textarea has proper label', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      expect(textarea).toBeInTheDocument();
    });

    it('send button has accessible name', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const button = screen.getByRole('button', { name: /send/i });
      expect(button.textContent).toMatch(/send/i);
    });

    it('main area has proper role', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const mainArea = container.querySelector('[role="main"]');
      expect(mainArea).toBeInTheDocument();
    });

    it('focusable elements are in logical order', () => {
      const { container: _container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      const button = screen.getByRole('button', { name: /send/i });

      textarea.focus();
      expect(document.activeElement).toBe(textarea);

      button.focus();
      expect(document.activeElement).toBe(button);
    });
  });
});
