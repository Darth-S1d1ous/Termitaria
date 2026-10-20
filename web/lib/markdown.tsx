import { Fragment, type ReactNode } from "react";

type Person = { id: string; display_name: string };

// 消息正文。支持加粗、行内代码、链接、表格、列表，以及 @同事 点开私聊。
export function MessageBody({
  text,
  agents,
  onOpen,
}: {
  text: string;
  agents: Person[];
  onOpen: (agentId: string) => void;
}) {
  return (
    <div className="md">
      {splitFences(text).map((part, index) => (
        <Fragment key={index}>{renderBlocks(part, agents, onOpen)}</Fragment>
      ))}
    </div>
  );
}

function splitFences(text: string): string[] {
  // ```markdown 围栏里的内容按正文渲染，不显示成源码。
  const fence = /^```(?:markdown|md)[^\n]*\n([\s\S]*?)\n```[ \t]*$/gim;
  const parts: string[] = [];
  let cursor = 0;
  for (const match of text.matchAll(fence)) {
    const start = match.index ?? 0;
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(match[1]);
    cursor = start + match[0].length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts.filter((part) => part.trim());
}

function renderBlocks(text: string, agents: Person[], onOpen: (agentId: string) => void) {
  return text.split(/\n{2,}/).map((block, index) => (
    <Block key={index} text={block} agents={agents} onOpen={onOpen} />
  ));
}

function Block({
  text,
  agents,
  onOpen,
}: {
  text: string;
  agents: Person[];
  onOpen: (agentId: string) => void;
}) {
  const lines = text.split("\n");
  const filled = lines.filter((line) => line.trim());
  const heading = filled.length === 1 ? filled[0].match(/^(#{1,6})\s+(\S.*)$/) : null;
  if (heading) {
    const level = Math.min(heading[1].length, 3);
    const Tag = `h${level}` as "h1" | "h2" | "h3";
    return <Tag>{renderInline(heading[2], agents, onOpen)}</Tag>;
  }
  if (filled.length >= 2 && filled.every((line) => line.includes("|"))) {
    return <Table lines={filled} agents={agents} onOpen={onOpen} />;
  }
  if (filled.length > 0 && filled.every((line) => /^\s*(?:[-*]|\d+\.)\s+/.test(line))) {
    const ordered = filled.every((line) => /^\s*\d+\.\s+/.test(line));
    const items = filled.map((line) => line.replace(/^\s*(?:[-*]|\d+\.)\s+/, ""));
    const List = ordered ? "ol" : "ul";
    return (
      <List>
        {items.map((item, index) => (
          <li key={index}>{renderInline(item, agents, onOpen)}</li>
        ))}
      </List>
    );
  }
  return (
    <p>
      {lines.map((line, index) => (
        <span key={index}>
          {index > 0 ? <br /> : null}
          {renderInline(line, agents, onOpen)}
        </span>
      ))}
    </p>
  );
}

function Table({
  lines,
  agents,
  onOpen,
}: {
  lines: string[];
  agents: Person[];
  onOpen: (agentId: string) => void;
}) {
  const rows = lines
    .filter((line) => !/^\s*\|?\s*:?-{3,}/.test(line))
    .map((line) => splitRow(line));
  if (rows.length === 0) return <p>{lines.join("\n")}</p>;
  const [head, ...body] = rows;
  return (
    <table>
      <thead>
        <tr>
          {head.map((cell, index) => (
            <th key={index}>{renderInline(cell, agents, onOpen)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.map((row, index) => (
          <tr key={index}>
            {row.map((cell, cellIndex) => (
              <td key={cellIndex}>{renderInline(cell, agents, onOpen)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function splitRow(line: string): string[] {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  return text.split("|").map((cell) => cell.trim());
}

function renderInline(text: string, agents: Person[], onOpen: (agentId: string) => void): ReactNode[] {
  const pieces = text.split(/(\*\*[^*]+\*\*)/g);
  return pieces.map((piece, index) => {
    if (piece.startsWith("**") && piece.endsWith("**") && piece.length >= 4) {
      return <strong key={index}>{renderPlain(piece.slice(2, -2), agents, onOpen)}</strong>;
    }
    return <span key={index}>{renderPlain(piece, agents, onOpen)}</span>;
  });
}

function renderPlain(text: string, agents: Person[], onOpen: (agentId: string) => void): ReactNode[] {
  const names = agents
    .flatMap((agent) => [
      { id: agent.id, label: agent.display_name },
      { id: agent.id, label: agent.id },
    ])
    .filter((agent) => agent.label)
    .sort((a, b) => b.label.length - a.label.length);
  const mention = names.map((agent) => `@${escapeReg(agent.label)}`).join("|");
  const source =
    (mention ? `${mention}|` : "") +
    "`[^`]+`|\\[[^\\]]+\\]\\(https?:\\/\\/[^\\s)]+\\)|https?:\\/\\/[^\\s)]+";
  const pattern = new RegExp(`(${source})`, "g");
  const parts = text.split(pattern);
  return parts.map((part, index) => {
    if (!part) return null;
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    const linked = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    if (linked) {
      return (
        <a key={index} href={linked[2]} target="_blank" rel="noreferrer">
          {linked[1]}
        </a>
      );
    }
    if (/^https?:\/\//.test(part)) {
      return (
        <a key={index} href={part} target="_blank" rel="noreferrer">
          {part}
        </a>
      );
    }
    if (part.startsWith("@")) {
      const label = part.slice(1);
      const agent = names.find((item) => item.label === label);
      if (agent) {
        return (
          <button key={index} type="button" className="mention" onClick={() => onOpen(agent.id)}>
            @{agent.label}
          </button>
        );
      }
    }
    return <span key={index}>{part}</span>;
  });
}

function escapeReg(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
