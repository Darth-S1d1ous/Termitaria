"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createProject,
  deleteProject,
  liveSocketURL,
  loadChats,
  loadMessages,
  loadProjects,
  loadTopology,
} from "../lib/api";
import { AnimalIcon, markFor } from "../lib/animals";
import { colorFor, formatTime, initials, preview, statusLine } from "../lib/format";
import type { Chat, ChatMessage, MeshEdge, Project, View } from "../lib/types";
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
import { SwarmView } from "./SwarmView";

type LiveFrame = {
  type: string;
  chat_id?: string;
  session_id?: string;
  message_id?: string;
  seq?: number;
  text?: string;
  agent_id?: string;
  state?: string;
  from?: string;
  to?: string;
  message?: ChatMessage | string;
  client_msg_id?: string;
};

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
  const [waiting, setWaiting] = useState<Record<string, boolean>>({});
  const [presence, setPresence] = useState<Record<string, string>>({});
  const [edges, setEdges] = useState<MeshEdge[]>([]);
  const [mention, setMention] = useState<string | null>(null);
  const [focusSession, setFocusSession] = useState<string | null>(null);
  const chatIdRef = useRef(chatId);
  chatIdRef.current = chatId;
  const wsRef = useRef<WebSocket | null>(null);
  const seqRef = useRef<Record<string, number>>({});
  const onFrame = useRef<(frame: LiveFrame) => void>(() => {});
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
    setMention(null);
    setFocusSession(null);
    setWaiting({});
    loadChats(projectId)
      .then((data) => {
        if (!cancel) setChats(data.chats);
      })
      .catch((err: unknown) => {
        if (!cancel) setError(err instanceof Error ? err.message : "Could not load chats");
      });
    loadTopology(projectId)
      .then((data) => {
        if (cancel) return;
        setEdges(data.sessions);
        setPresence(Object.fromEntries(data.agents.map((agent) => [agent.id, agent.presence])));
      })
      .catch((err: unknown) => {
        if (!cancel) setError(err instanceof Error ? err.message : "Could not load swarm");
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

  onFrame.current = (frame) => {
    if (frame.type === "presence" && frame.agent_id && frame.state) {
      setPresence((current) => ({ ...current, [frame.agent_id as string]: frame.state as string }));
      return;
    }
    if (frame.type === "topology.session_opened" && frame.session_id && frame.from && frame.to) {
      const sessionId = frame.session_id;
      setEdges((current) =>
        current.some((edge) => edge.id === sessionId)
          ? current
          : [...current, { id: sessionId, from: frame.from as string, to: frame.to as string }],
      );
      return;
    }
    if (frame.type === "topology.session_closed" && frame.session_id) {
      setEdges((current) => current.filter((edge) => edge.id !== frame.session_id));
      if (focusSession === frame.session_id) setFocusSession(null);
      return;
    }
    if (frame.type === "error") {
      const chat = frame.chat_id ?? "";
      setWaiting((current) => {
        const next = { ...current };
        delete next[chat];
        return next;
      });
      if (chatIdRef.current === chat) {
        const pendingId = frame.client_msg_id ? `m_${frame.client_msg_id}` : "";
        if (pendingId) setMessages((current) => current.filter((item) => item.id !== pendingId));
        setError(typeof frame.message === "string" ? frame.message : "Could not send");
      }
      return;
    }
    if (frame.type === "message.delta" && frame.chat_id && frame.message_id && frame.text) {
      const seq = frame.seq ?? 0;
      const seen = seqRef.current[frame.message_id] ?? 0;
      if (seq <= seen) return;
      seqRef.current[frame.message_id] = seq;
      if (chatIdRef.current !== frame.chat_id) return;
      setMessages((current) => {
        const index = current.findIndex((item) => item.id === frame.message_id);
        if (index < 0) {
          return [...current, {
            id: frame.message_id as string,
            role: "agent",
            text: frame.text as string,
            created_at_ms: Date.now(),
            agent_id: frame.agent_id,
            sender: frame.agent_id,
            session_id: frame.session_id,
            streaming: true,
          }];
        }
        const next = current.slice();
        next[index] = { ...next[index], text: next[index].text + frame.text, streaming: true };
        return next;
      });
      return;
    }
    if (frame.type === "message.final" && frame.chat_id && frame.message && typeof frame.message !== "string") {
      const message = frame.message;
      delete seqRef.current[message.id];
      setChats((current) =>
        current.map((item) =>
          item.id === frame.chat_id
            ? { ...item, last_message: message.text, last_role: message.role, last_message_at_ms: message.created_at_ms }
            : item,
        ),
      );
      if (message.role === "agent") {
        setWaiting((current) => {
          const next = { ...current };
          delete next[frame.chat_id as string];
          return next;
        });
      }
      if (chatIdRef.current !== frame.chat_id) return;
      setMessages((current) => {
        const index = current.findIndex((item) => item.id === message.id);
        const clean = { ...message, streaming: false };
        if (index < 0) return [...current, clean];
        const next = current.slice();
        next[index] = clean;
        return next;
      });
    }
  };

  useEffect(() => {
    if (!projectId) return;
    const ws = new WebSocket(liveSocketURL(projectId));
    wsRef.current = ws;
    ws.onmessage = (event) => {
      try {
        onFrame.current(JSON.parse(event.data) as LiveFrame);
      } catch {
        // 半包或非 JSON 帧丢掉，下一条 final 会把时间线补齐。
      }
    };
    return () => {
      ws.close();
      if (wsRef.current === ws) wsRef.current = null;
    };
  }, [projectId]);

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

  function onSend() {
    const text = draft.trim();
    const sock = wsRef.current;
    if (!text || !projectId || !chat || !chat.enabled || waiting[chat.id]) return;
    if (!sock || sock.readyState !== WebSocket.OPEN) {
      setError("连接还没就绪");
      return;
    }
    const targetChatId = chat.id;
    const mentioned = chat.kind === "channel" && mention
      ? project?.agents.find((agent) => agent.id === mention)
      : undefined;
    const sent = mentioned ? `@${mentioned.display_name}${text}` : text;
    const clientMsgId = crypto.randomUUID().replace(/-/g, "");
    const pending: ChatMessage = {
      id: `m_${clientMsgId}`,
      role: "user",
      text: sent,
      created_at_ms: Date.now(),
    };
    setDraft("");
    setMention(null);
    setError("");
    setWaiting((current) => ({ ...current, [targetChatId]: true }));
    setMessages((current) => [...current, pending]);
    sock.send(JSON.stringify({
      type: "message.send",
      chat_id: targetChatId,
      mention_agent_id: mentioned?.id ?? "",
      client_msg_id: clientMsgId,
      text: sent,
    }));
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
            aria-label="Swarm"
            onClick={() => setView("graph")}
          >
            <GraphIcon />
          </button>
          <button
            className={`rail-btn${view === "lifecycle" ? " active" : ""}`}
            type="button"
            aria-label="Memory inspector"
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
                <Avatar chat={item} ids={project?.agents.map((agent) => agent.id) ?? []} />
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
          sending={Boolean(chat && waiting[chat.id])}
          busy={Boolean(chat && waiting[chat.id])}
          error={chat ? error : ""}
          agents={project?.agents ?? []}
          mention={chat?.kind === "channel" ? mention : null}
          onMention={setMention}
          presence={presence}
          focusSession={chat?.kind === "channel" ? focusSession : null}
          onClearFocus={() => setFocusSession(null)}
          onNotice={setError}
          onDraft={setDraft}
          onSend={onSend}
          onBack={() => setChatId(null)}
        />
      )}

      {view === "graph" && (
        <SwarmView
          agents={project?.agents ?? []}
          edges={edges}
          presence={presence}
          focus={focusSession}
          onFocus={(sessionId) => {
            setFocusSession(sessionId);
            if (sessionId) {
              setChatId("channel");
              setView("chats");
            }
          }}
        />
      )}
      {view === "lifecycle" && (
        <main className="blank" aria-label="Memory inspector">
          <div className="empty">
            <strong>Memory Inspector</strong>
            <p>记忆服务还没接上</p>
          </div>
        </main>
      )}

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

function Avatar({ chat, ids = [], small = false }: { chat: Chat; ids?: string[]; small?: boolean }) {
  if (chat.kind === "saved") {
    return (
      <span className={`avatar saved${small ? " sm" : ""}`}>
        <BookmarkIcon />
      </span>
    );
  }
  if (chat.kind === "channel") {
    return <span className={`avatar channel${small ? " sm" : ""}`}>#</span>;
  }
  const mark = markFor(chat.id, ids.length ? ids : [chat.id]);
  return (
    <span className={`avatar${small ? " sm" : ""}`} style={{ background: mark.color }} title={mark.name}>
      <AnimalIcon path={mark.path} />
    </span>
  );
}

const PRESENCE_LABEL: Record<string, string> = {
  online: "在线",
  thinking: "思考中",
  offline: "离线",
};

function Conversation({
  chat,
  messages,
  draft,
  sending,
  busy,
  error,
  agents,
  mention,
  onMention,
  presence,
  focusSession,
  onClearFocus,
  onNotice,
  onDraft,
  onSend,
  onBack,
}: {
  chat: Chat | null;
  messages: ChatMessage[];
  draft: string;
  sending: boolean;
  busy: boolean;
  error: string;
  agents: Project["agents"];
  mention: string | null;
  onMention: (agentId: string | null) => void;
  presence: Record<string, string>;
  focusSession: string | null;
  onClearFocus: () => void;
  onNotice: (message: string) => void;
  onDraft: (value: string) => void;
  onSend: () => void;
  onBack: () => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    setPicking(false);
  }, [chat?.id]);

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

  const enabled = chat.enabled && !busy;
  const shown = focusSession
    ? messages.filter((item) => !item.session_id || item.session_id === focusSession)
    : messages;
  const mentioned = agents.find((agent) => agent.id === mention) ?? null;
  const streaming = shown.some((item) => item.streaming);
  const state = chat.kind === "agent" ? presence[chat.id] : "";
  const header = chat.kind === "agent" && (sending || state === "thinking")
    ? "thinking…"
    : state === "online" || state === "offline"
      ? state
      : statusLine(chat);

  return (
    <section className="conversation" aria-label={chat.title}>
      <header className="conv-header">
        <button className="icon-btn back" type="button" aria-label="Back" onClick={onBack}>
          <BackIcon />
        </button>
        <Avatar chat={chat} ids={agents.map((agent) => agent.id)} small />
        <div className="who">
          <strong>{chat.title}</strong>
          <span>{header}</span>
        </div>
        {chat.kind === "channel" ? (
          <div className="roster">
            {agents.map((agent) => {
              const state = presence[agent.id] ?? "offline";
              const mark = markFor(agent.id, agents.map((item) => item.id));
              return (
                <span
                  key={agent.id}
                  className="roster-agent"
                  title={`${agent.display_name} ${PRESENCE_LABEL[state] ?? state}`}
                >
                  <span className="avatar sm" style={{ background: mark.color }}>
                    <AnimalIcon path={mark.path} />
                  </span>
                  <i className={`pip ${state}`} />
                </span>
              );
            })}
          </div>
        ) : null}
      </header>
      {focusSession ? (
        <p className="focus-bar">
          只看这一条 session
          <button type="button" onClick={onClearFocus}>显示全部</button>
        </p>
      ) : null}
      <div className="thread">
        {shown.map((message, index) => {
          const prev = shown[index - 1];
          const grouped = Boolean(prev && prev.role === message.role && prev.agent_id === message.agent_id);
          return (
            <article key={message.id} className={`msg ${message.role}${grouped ? " grouped" : ""}`}>
              {chat.kind === "channel" && message.role === "agent" && !grouped ? (
                <span className="msg-name">{message.sender || message.agent_id}</span>
              ) : null}
              <div className="bubble">
                {message.text}
                <time dateTime={new Date(message.created_at_ms).toISOString()}>{formatTime(message.created_at_ms)}</time>
              </div>
            </article>
          );
        })}
        {sending && !streaming ? (
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
        {mentioned ? (
          <div className="mention-chip">
            @{mentioned.display_name}
            <button type="button" aria-label="Remove mention" onClick={() => onMention(null)}>×</button>
          </div>
        ) : null}
        {picking && chat.kind === "channel" ? (
          <div className="mention-list">
            {agents.map((agent) => (
              <button
                key={agent.id}
                type="button"
                onClick={() => {
                  onMention(agent.id);
                  setPicking(false);
                }}
              >
                {agent.display_name}
              </button>
            ))}
          </div>
        ) : null}
        <textarea
          ref={boxRef}
          aria-label="Message"
          rows={1}
          placeholder={chat.enabled ? "Message" : "Saved Messages"}
          value={draft}
          disabled={!chat.enabled || busy}
          onChange={(event) => {
            const value = event.target.value;
            if (chat.kind !== "channel" || !value.includes("@")) {
              onDraft(value);
              return;
            }
            onDraft(value.replaceAll("@", ""));
            if (mention) {
              onNotice("一条消息只能 @ 一个 agent");
              return;
            }
            setPicking(true);
          }}
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
