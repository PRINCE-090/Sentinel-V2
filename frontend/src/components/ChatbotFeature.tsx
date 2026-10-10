import { FormEvent, useEffect, useRef, useState } from "react";
import { Bot, Send } from "lucide-react";
import { sendBudgetChatMessage, SentinelApiError } from "../api";

type Message = {
  author: "assistant" | "user";
  text: string;
};

function InlineText({ text }: { text: string }) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : (
      <span key={index}>{part}</span>
    ),
  );
}

function ChatMessageContent({ text }: { text: string }) {
  const blocks = text.trim().split(/\n\s*\n/).filter(Boolean);

  return blocks.map((block, index) => {
    const lines = block.split("\n").filter(Boolean);
    const bulletItems = lines.map((line) => line.match(/^[-*]\s+(.+)$/)?.[1]);
    const orderedItems = lines.map((line) => line.match(/^\d+\.\s+(.+)$/)?.[1]);

    if (bulletItems.every(Boolean)) {
      return <ul key={index}>{bulletItems.map((item, itemIndex) => <li key={itemIndex}><InlineText text={item!} /></li>)}</ul>;
    }
    if (orderedItems.every(Boolean)) {
      return <ol key={index}>{orderedItems.map((item, itemIndex) => <li key={itemIndex}><InlineText text={item!} /></li>)}</ol>;
    }
    return <p key={index}><InlineText text={lines.join(" ")} /></p>;
  });
}

const WELCOME_MESSAGE: Message = {
  author: "assistant",
  text: "Hi, I’m Sentinel Budget Advisor. Ask me about budgeting, savings, debt, or your financial goals.",
};

export default function ChatbotFeature() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([WELCOME_MESSAGE]);
  const [isSending, setIsSending] = useState(false);
  const [sessionId] = useState(() => crypto.randomUUID());
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const latestAssistantMessageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const resizeComposer = () => {
    const composer = inputRef.current;
    if (!composer) return;
    composer.style.height = "auto";
    composer.style.height = `${Math.min(composer.scrollHeight, 144)}px`;
  };

  useEffect(() => {
    const latestMessage = messages[messages.length - 1];
    if (!latestMessage || latestMessage.author !== "assistant" || messages.length === 1) return;

    const scrollTimer = window.setTimeout(() => {
      latestAssistantMessageRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 50);

    return () => window.clearTimeout(scrollTimer);
  }, [messages]);

  const sendMessage = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = input.trim();
    if (!message) return;
    setMessages((current) => [...current, { author: "user", text: message }]);
    setInput("");
    window.requestAnimationFrame(resizeComposer);
    setIsSending(true);
    try {
      const response = await sendBudgetChatMessage(sessionId, message);
      const warningText = response.warnings.length ? `\n\nNote: ${response.warnings.join(" ")}` : "";
      setMessages((current) => [...current, { author: "assistant", text: `${response.reply}${warningText}` }]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          author: "assistant",
          text: error instanceof SentinelApiError ? error.message : "I couldn't process that message. Please try again.",
        },
      ]);
    } finally {
      setIsSending(false);
    }
  };

  return (
    <section className="chatbot-workspace" aria-labelledby="chatbot-title">
      <div className="chatbot-intro">
        <span className="chatbot-label"><Bot size={14} /> SENTINEL ASSISTANT</span>
        <h1 id="chatbot-title">Ask Sentinel.</h1>
        <p>Get practical guidance on budgeting, saving, debt, and financial goals.</p>
      </div>
      <div className="chatbot-panel">
        <div className="chatbot-messages" aria-live="polite">
          {messages.map((message, index) => (
            <div
              ref={index === messages.length - 1 && message.author === "assistant" ? latestAssistantMessageRef : undefined}
              className={`chatbot-message chatbot-message--${message.author}`}
              key={`${message.author}-${index}`}
            >
              <ChatMessageContent text={message.text} />
            </div>
          ))}
          {isSending && <p className="chatbot-message chatbot-message--assistant chatbot-message--pending">Thinking…</p>}
        </div>
        <form className="chatbot-composer" onSubmit={sendMessage}>
          <label className="sr-only" htmlFor="chatbot-input">Ask Sentinel Assistant</label>
          <textarea
            ref={inputRef}
            id="chatbot-input"
            rows={1}
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
              resizeComposer();
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }}
            placeholder="Ask Sentinel…"
          />
          <button type="submit" aria-label="Send message" disabled={isSending}><Send size={16} /></button>
        </form>
      </div>
    </section>
  );
}

export function ChatbotLoading() {
  return (
    <section className="chatbot-loading" aria-live="polite" aria-label="Opening Sentinel Assistant">
      <Bot size={28} />
      <p>SENTINEL ASSISTANT</p>
      <span>Preparing your secure workspace…</span>
    </section>
  );
}
