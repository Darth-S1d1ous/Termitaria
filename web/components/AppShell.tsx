"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createProject,
  deleteProject,
  loadChats,
  loadMessages,
  loadProjects,
  sendMessage,
} from "../lib/api";
import { colorFor, formatTime, initials, preview, statusLine } from "../lib/format";
import type { Chat, ChatMessage, Project, View } from "../lib/types";
import {
  BackIcon,
  BookmarkIcon,
  ChatsIcon,
  GraphIcon,
  LifecycleIcon,
  PinIcon,
  PlusIcon,
  SearchIcon,
  SendIcon,
  TrashIcon,
} from "./icons";

export function AppShell() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [view, setView] = useState<View>("chats");
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [projectName, setProjectName] = useState("");
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [savingProject, setSavingProject] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const [booting, setBooting] = useState(true);

  const project = projects.find((item) => item.id === projectId) ?? null;
  const chat = chats.find((item) => item.id === chatId) ?? null;
  const visibleChats = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return chats;
    return chats.filter((item) => item.title.toLowerCase().includes(needle));
  }, [chats, query]);

  useEffect(() => {
    let cancel = false;
    loadProjects()
      .then((data) => {
        if (cancel) return;
        setProjects(data.projects);
        setProjectId((current) => current ?? data.projects[0]?.id ?? null);
      })
      .catch((err: unknown) => {
        if (!cancel) setError(err instanceof Error ? err.message : "Could not load projects");
      })
      .finally(() => {
        if (!cancel) setBooting(false);
      });
    return () => {
      cancel = true;
    };
  }, []);

  useEffect(() => {
    if (!projectId) {
      setChats([]);
      setChatId(null);
      setMessages([]);
      return;
    }
    let cancel = false;
    setChatId(null);
    setMessages([]);
    setQuery("");
    setConfirmDelete(false);
    loadChats(projectId)
      .then((data) => {
        if (!cancel) setChats(data.chats);
      })
      .catch((err: unknown) => {
        if (!cancel) setError(err instanceof Error ? err.message : "Could not load chats");
      });
    return () => {
      cancel = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !chatId) return;
    let cancel = false;
    loadMessages(projectId, chatId)
      .then((data) => {
        if (!cancel) setMessages(data.messages);
      })
      .catch((err: unknown) => {
        if (!cancel) setError(err instanceof Error ? err.message : "Could not load messages");
      });
    return () => {
      cancel = true;
    };
  }, [projectId, chatId]);

  async function onCreateProject() {
    const name = projectName.trim();
    if (!name || savingProject) return;
    setSavingProject(true);
    setError("");
    try {
      const created = await createProject(name);
      setProjects((current) => [...current, created]);
      setChatId(null);
      setProjectId(created.id);
      setProjectName("");
      setView("chats");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not create project");
    } finally {
      setSavingProject(false);
    }
  }

  async function onDeleteProject() {
    if (!projectId) return;
    setError("");
    try {
      await deleteProject(projectId);
      const next = projects.filter((item) => item.id !== projectId);
      setProjects(next);
      setChatId(null);
      setProjectId(next[0]?.id ?? null);
      setConfirmDelete(false);
      setView("chats");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not delete project");
    }
  }

  async function onSend() {
    const text = draft.trim();
    if (!text || !projectId || !chat || !chat.enabled || sending) return;
    const pending: ChatMessage = {
      id: "pending-user",
      role: "user",
      text,
      created_at_ms: Date.now(),
    };
    setDraft("");
    setSending(true);
    setError("");
    setMessages((current) => [...current, pending]);
    try {
      const turn = await sendMessage(projectId, chat.id, text);
      setMessages((current) => [...current.filter((item) => item.id !== pending.id), turn.user, turn.agent]);
      const data = await loadChats(projectId);
      setChats(data.chats);
    } catch (err: unknown) {
      setMessages((current) => current.filter((item) => item.id !== pending.id));
      setDraft(text);
      setError(err instanceof Error ? err.message : "Could not send");
    } finally {
      setSending(false);
    }
  }

  const solo = view !== "chats";
  const chatOpen = view === "chats" && chatId !== null;

  return (
    <div className={`shell${solo ? " solo" : ""}${chatOpen ? " chat-open" : ""}`}>
      <aside className="rail">
        <button className="mark" type="button" aria-label="Chats" onClick={() => setView("chats")}>
          T
        </button>
        <div className="rail-scroll">
          {projects.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`project-btn${item.id === projectId ? " active" : ""}`}
              style={{ background: colorFor(item.id) }}
              aria-label={item.name}
              aria-current={item.id === projectId ? "true" : undefined}
              title={item.name}
              onClick={() => {
                setProjectId(item.id);
                setChatId(null);
                setView("chats");
              }}
            >
              {initials(item.name)}
            </button>
          ))}
          <button
            className={`plus${view === "create" ? " active" : ""}`}
            type="button"
            aria-label="New project"
            onClick={() => setView("create")}
          >
            <PlusIcon />
          </button>
        </div>
        <div className="rail-tools">
          <button
            className={`rail-btn${view === "graph" ? " active" : ""}`}
            type="button"
            aria-label="Knowledge graph"
            onClick={() => setView("graph")}
          >
            <GraphIcon />
          </button>
          <button
            className={`rail-btn${view === "lifecycle" ? " active" : ""}`}
            type="button"
            aria-label="Lifecycle"
            onClick={() => setView("lifecycle")}
          >
            <LifecycleIcon />
          </button>
        </div>
      </aside>

      {view === "chats" && (
        <section className="chat-list" aria-label="Chats">
          <header className="pane-header">
            {confirmDelete ? (
              <div className="confirm-delete">
                <span>Delete {project?.name}?</span>
                <button className="text-btn" type="button" onClick={() => setConfirmDelete(false)}>
                  Cancel
                </button>
                <button className="text-btn danger" type="button" onClick={onDeleteProject}>
                  Delete
                </button>
              </div>
            ) : (
              <>
                <div className="pane-title">
                  <h1>{project?.name ?? (booting ? "Loading" : "Termitaria")}</h1>
                  {project?.goal ? <p>{project.goal}</p> : null}
                </div>
                {project ? (
                  <button className="icon-btn" type="button" aria-label="Delete project" onClick={() => setConfirmDelete(true)}>
                    <TrashIcon />
                  </button>
                ) : null}
              </>
            )}
          </header>
          <label className="search">
            <SearchIcon />
            <input
              value={query}
              placeholder="Search"
              aria-label="Search"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          {error && !chat ? <p className="banner">{error}</p> : null}
          <div className="rows">
            {visibleChats.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`chat-row${item.id === chatId ? " active" : ""}`}
                aria-current={item.id === chatId ? "true" : undefined}
                onClick={() => setChatId(item.id)}
              >
                <Avatar chat={item} />
                <span className="chat-main">
                  <span className="chat-topline">
                    <strong>{item.title}</strong>
                    {item.pinned ? <PinIcon className="pin" /> : null}
                    {item.last_message_at_ms ? <time className="time">{formatTime(item.last_message_at_ms)}</time> : null}
                  </span>
                  <span className="chat-subline">
                    <span className="preview">{preview(item)}</span>
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {view === "chats" && (
        <Conversation
          chat={chat}
          messages={messages}
          draft={draft}
          sending={sending}
          error={chat ? error : ""}
          onDraft={setDraft}
          onSend={onSend}
          onBack={() => setChatId(null)}
        />
      )}

      {view === "graph" && <main className="blank" aria-label="Knowledge graph" />}
      {view === "lifecycle" && <main className="blank" aria-label="Lifecycle" />}

      {view === "create" && (
        <section className="builder" aria-label="New project">
          <header className="pane-header">
            <h1>New project</h1>
            <div className="header-actions">
              <button className="text-btn" type="button" onClick={() => setView("chats")}>
                Cancel
              </button>
              <button
                className="primary"
                type="button"
                disabled={!projectName.trim() || savingProject}
                onClick={onCreateProject}
              >
                Create
              </button>
            </div>
          </header>
          {error ? <p className="banner">{error}</p> : null}
          <div className="builder-body">
            <label className="name-field">
              <span>Project name</span>
              <input
                value={projectName}
                autoFocus
                aria-label="Project name"
                onChange={(event) => setProjectName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") onCreateProject();
                }}
              />
            </label>
            <div className="slots">
              <div className="slot" aria-label="Team">
                <span className="slot-label">Team</span>
              </div>
              <div className="slot" aria-label="Initial knowledge graph">
                <span className="slot-label">Knowledge graph</span>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

function Avatar({ chat, small = false }: { chat: Chat; small?: boolean }) {
  if (chat.kind === "saved") {
    return (
      <span className={`avatar saved${small ? " sm" : ""}`}>
        <BookmarkIcon />
      </span>
    );
  }
  return (
    <span className={`avatar${small ? " sm" : ""}`} style={{ background: colorFor(chat.id) }}>
      {initials(chat.title)}
    </span>
  );
}

function Conversation({
  chat,
  messages,
  draft,
  sending,
  error,
  onDraft,
  onSend,
  onBack,
}: {
  chat: Chat | null;
  messages: ChatMessage[];
  draft: string;
  sending: boolean;
  error: string;
  onDraft: (value: string) => void;
  onSend: () => void;
  onBack: () => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, sending, chat?.id]);

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    box.style.height = "0px";
    box.style.height = `${Math.min(box.scrollHeight, 140)}px`;
  }, [draft, chat?.id]);

  if (!chat) {
    return (
      <section className="conversation">
        <div className="empty">
          <strong>Termitaria</strong>
          <p>Select a chat to start messaging</p>
        </div>
      </section>
    );
  }

  const enabled = chat.enabled && !sending;

  return (
    <section className="conversation" aria-label={chat.title}>
      <header className="conv-header">
        <button className="icon-btn back" type="button" aria-label="Back" onClick={onBack}>
          <BackIcon />
        </button>
        <Avatar chat={chat} small />
        <div className="who">
          <strong>{chat.title}</strong>
          <span>{sending ? "thinking…" : statusLine(chat)}</span>
        </div>
      </header>
      <div className="thread">
        {messages.map((message, index) => {
          const grouped = index > 0 && messages[index - 1].role === message.role;
          return (
            <article key={message.id} className={`msg ${message.role}${grouped ? " grouped" : ""}`}>
              <div className="bubble">
                {message.text}
                <time dateTime={new Date(message.created_at_ms).toISOString()}>{formatTime(message.created_at_ms)}</time>
              </div>
            </article>
          );
        })}
        {sending ? (
          <article className="msg agent" aria-label="thinking">
            <div className="bubble thinking">
              <i /><i /><i />
            </div>
          </article>
        ) : null}
        <div ref={endRef} />
      </div>
      {error ? <p className="banner">{error}</p> : null}
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          onSend();
        }}
      >
        <textarea
          ref={boxRef}
          aria-label="Message"
          rows={1}
          placeholder={chat.enabled ? "Message" : "Saved Messages"}
          value={draft}
          disabled={!chat.enabled || sending}
          onChange={(event) => onDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSend();
            }
          }}
        />
        <button className="send" type="submit" aria-label="Send" disabled={!enabled || !draft.trim()}>
          <SendIcon />
        </button>
      </form>
    </section>
  );
}
