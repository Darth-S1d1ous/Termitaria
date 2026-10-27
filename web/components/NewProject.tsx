"use client";

import { useRef, useState } from "react";
import type { SwarmSpecDoc } from "../lib/api";
import { randomId } from "../lib/id";
import type { Seed, SeedKind } from "../lib/seeds";

const STEPS = ["Name", "Team", "Knowledge"] as const;
const TEXT_FILE = /\.(txt|md|markdown|csv|json|tsv|log|yaml|yml|html|xml|tex)$/i;
const MAX_TEXT_SEED = 20000;

type Member = { id: string; name: string; kind: string };

// 从 SwarmSpec 的 agents 段读出展示名。模板卡片和右侧名单都用这份结果。
function membersFromSpec(yaml: string): Member[] {
  const members: Member[] = [];
  let current: Member | null = null;
  let inAgents = false;
  for (const line of yaml.split("\n")) {
    if (/^agents:/.test(line)) {
      inAgents = true;
      continue;
    }
    if (inAgents && /^[A-Za-z]/.test(line)) break;
    if (!inAgents) continue;
    const id = line.match(/^\s+- id:\s*(\S+)/);
    if (id) {
      current = { id: id[1], name: id[1], kind: "" };
      members.push(current);
      continue;
    }
    if (!current) continue;
    const display = line.match(/^\s+displayName:\s*(.+?)\s*$/);
    if (display) current.name = display[1];
    const kind = line.match(/^\s+kind:\s*(\S+)/);
    if (kind) current.kind = kind[1];
  }
  return members;
}

function goalFromSpec(yaml: string): string {
  const lines = yaml.split("\n");
  const start = lines.findIndex((line) => line.startsWith("goal:"));
  if (start < 0) return "";
  const inline = lines[start].slice("goal:".length).trim();
  if (inline && inline !== "|" && inline !== ">") return inline.replace(/^["']|["']$/g, "");
  const body: string[] = [];
  for (let index = start + 1; index < lines.length; index += 1) {
    if (!lines[index].startsWith(" ") && !lines[index].startsWith("\t")) break;
    body.push(lines[index].trim());
  }
  return body.join(" ");
}

function roleLabel(kind: string): string {
  if (kind === "ORCHESTRATOR") return "Lead";
  return "Specialist";
}

export function NewProject({
  specs,
  saving,
  error,
  onCancel,
  onCreate,
}: {
  specs: SwarmSpecDoc[];
  saving: boolean;
  error: string;
  onCancel: () => void;
  onCreate: (name: string, spec: string, seeds: Seed[]) => void;
}) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [specText, setSpecText] = useState("");
  const [seeds, setSeeds] = useState<Seed[]>([]);
  const [note, setNote] = useState("");
  const [link, setLink] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);

  function goTeam() {
    if (!name.trim()) return;
    setStep(1);
  }

  function goKnowledge() {
    if (!specText.trim()) return;
    setStep(2);
  }

  function addSeed(kind: SeedKind, title: string, body: string) {
    const cleanTitle = title.trim();
    const cleanBody = body.trim();
    if (!cleanTitle && !cleanBody) return;
    setSeeds((current) => [
      ...current,
      { id: randomId(), kind, title: cleanTitle || cleanBody, body: cleanBody },
    ]);
  }

  async function addFiles(list: FileList | null, kind: "file" | "image") {
    if (!list) return;
    const next = await Promise.all(
      Array.from(list).map(async (file): Promise<Seed> => {
        const image = file.type.startsWith("image/");
        const textLike =
          !image &&
          (!file.type || file.type.startsWith("text/") || file.type === "application/json" ||
            file.type === "application/xml" || TEXT_FILE.test(file.name));
        let body = file.type || kind;
        if (textLike) {
          try {
            body = (await file.text()).slice(0, MAX_TEXT_SEED);
          } catch {
            body = file.type || kind;
          }
        }
        return {
          id: randomId(),
          kind: image ? "image" : kind,
          title: file.name,
          body,
        };
      }),
    );
    setSeeds((current) => [...current, ...next]);
  }

  const primaryDisabled =
    saving || (step === 0 && !name.trim()) || (step === 1 && !specText.trim());
  const members = membersFromSpec(specText);
  const matched = specs.find((item) => item.yaml === specText);
  const goal = matched?.goal || goalFromSpec(specText);

  return (
    <section className="builder" aria-label="New project">
      <header className="pane-header">
        <div className="pane-title">
          <h1>{STEPS[step]}</h1>
          <p>{step === 0 ? "New project" : step === 1 ? "Build your team" : "Initial knowledge"}</p>
        </div>
        <ol className="step-marks">
          {STEPS.map((label, index) => (
            <li key={label} className={index === step ? "current" : index < step ? "done" : ""}>
              {label}
            </li>
          ))}
        </ol>
        <div className="header-actions">
          <button
            className="text-btn"
            type="button"
            onClick={() => (step === 0 ? onCancel() : setStep(step - 1))}
          >
            {step === 0 ? "Cancel" : "Back"}
          </button>
          <button
            className="primary"
            type="button"
            disabled={primaryDisabled}
            onClick={() => {
              if (step === 0) goTeam();
              else if (step === 1) goKnowledge();
              else onCreate(name.trim(), specText.trim(), seeds);
            }}
          >
            {step === 2 ? (saving ? "Creating" : "Create") : step === 0 ? "Confirm" : "Continue"}
          </button>
        </div>
      </header>
      {error ? <p className="banner">{error}</p> : null}
      {step === 0 && (
        <div className="wizard-center">
          <label className="name-field">
            <span>Project name</span>
            <input
              value={name}
              autoFocus
              aria-label="Project name"
              placeholder="Project name"
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") goTeam();
              }}
            />
          </label>
        </div>
      )}
      {step === 1 && (
        <div className="team-layout">
          <aside className="templates" aria-label="Templates">
            <span className="slot-label">Templates</span>
            {specs.length === 0 ? <p className="slot-hint">No templates yet</p> : null}
            {specs.map((item) => {
              const count = membersFromSpec(item.yaml).length;
              return (
                <button
                  key={item.name}
                  type="button"
                  className={`template-card${specText === item.yaml ? " selected" : ""}`}
                  onClick={() => setSpecText(item.yaml)}
                >
                  <strong>{item.name}</strong>
                  <em>{count === 1 ? "1 person" : `${count} people`}</em>
                  <span>{item.goal}</span>
                </button>
              );
            })}
          </aside>
          <div className={`team-preview${members.length === 0 ? " is-empty" : ""}`}>
            {members.length === 0 ? (
              <div className="team-empty">
                <strong>No team yet</strong>
                <p>Pick a template. The people on that team show up here.</p>
              </div>
            ) : (
              <>
                {goal ? <p className="team-goal">{goal}</p> : null}
                <span className="slot-label">{members.length === 1 ? "1 person" : `${members.length} people`}</span>
                <ul className="member-list">
                  {members.map((member) => {
                    const role = roleLabel(member.kind);
                    return (
                      <li key={member.id}>
                        <strong>{member.name}</strong>
                        <span>{role ? `${member.id} · ${role}` : member.id}</span>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
            <details className="spec-fold">
              <summary>Edit spec</summary>
              <label className="spec-slot">
                <span className="slot-label">SwarmSpec</span>
                <textarea
                  className="spec-yaml"
                  aria-label="SwarmSpec"
                  spellCheck={false}
                  value={specText}
                  placeholder={"apiVersion: termitaria/v1\nkind: SwarmSpec\nname: lab\ngoal: ...\nagents:"}
                  onChange={(event) => setSpecText(event.target.value)}
                />
              </label>
            </details>
          </div>
        </div>
      )}
      {step === 2 && (
        <div className="knowledge">
          <div className="knowledge-form">
            <p className="slot-hint">
              Add notes, links, files, or images. They stay with this project and its knowledge graph.
            </p>
            <div className="name-field">
              <div className="field-head">
                <label htmlFor="knowledge-text">Text</label>
                <button
                  className="text-btn"
                  type="button"
                  disabled={!note.trim()}
                  onClick={() => {
                    addSeed("text", note.trim().slice(0, 80), note);
                    setNote("");
                  }}
                >
                  Add text
                </button>
              </div>
              <textarea
                id="knowledge-text"
                aria-label="Knowledge text"
                value={note}
                placeholder="Paste a research question, notes, or source text"
                onChange={(event) => setNote(event.target.value)}
              />
            </div>
            <form
              className="link-row"
              onSubmit={(event) => {
                event.preventDefault();
                const url = link.trim();
                if (!url) return;
                addSeed("link", url, url);
                setLink("");
              }}
            >
              <label className="name-field">
                <span>Link</span>
                <input
                  aria-label="Knowledge link"
                  value={link}
                  placeholder="https://"
                  onChange={(event) => setLink(event.target.value)}
                />
              </label>
              <button className="text-btn" type="submit" disabled={!link.trim()}>
                Add link
              </button>
            </form>
            <div className="file-row">
              <button className="text-btn" type="button" onClick={() => fileRef.current?.click()}>
                Add files
              </button>
              <button className="text-btn" type="button" onClick={() => imageRef.current?.click()}>
                Add images
              </button>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              aria-label="Knowledge files"
              onChange={(event) => {
                void addFiles(event.target.files, "file");
                event.target.value = "";
              }}
            />
            <input
              ref={imageRef}
              type="file"
              accept="image/*"
              multiple
              hidden
              aria-label="Knowledge images"
              onChange={(event) => {
                void addFiles(event.target.files, "image");
                event.target.value = "";
              }}
            />
            </div>
          </div>
          <div className="knowledge-added">
            <span className="slot-label">{seeds.length === 0 ? "Added" : `${seeds.length} added`}</span>
            <ul className="seed-list">
              {seeds.length === 0 ? <li className="slot-hint">Nothing added yet</li> : null}
              {seeds.map((item) => (
                <li key={item.id}>
                  <span className="seed-kind">{item.kind}</span>
                  <div className="seed-copy">
                    <span className="seed-title">{item.title}</span>
                    {item.body && item.body !== item.title ? (
                      <span className="seed-body">{item.body}</span>
                    ) : null}
                  </div>
                  <button
                    className="text-btn"
                    type="button"
                    aria-label={`Remove ${item.title}`}
                    onClick={() => setSeeds((current) => current.filter((seed) => seed.id !== item.id))}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}
