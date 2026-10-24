"use client";

import { useRef, useState } from "react";
import type { SwarmSpecDoc } from "../lib/api";
import { randomId } from "../lib/format";
import type { Seed, SeedKind } from "../lib/seeds";

const STEPS = ["Name", "Team", "Knowledge"] as const;

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

  function addFiles(list: FileList | null, kind: "file" | "image") {
    if (!list) return;
    const next: Seed[] = [];
    for (const file of list) {
      next.push({
        id: randomId(),
        kind: file.type.startsWith("image/") ? "image" : kind,
        title: file.name,
        body: file.type || kind,
      });
    }
    setSeeds((current) => [...current, ...next]);
  }

  const primaryDisabled =
    saving || (step === 0 && !name.trim()) || (step === 1 && !specText.trim());

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
            {specs.map((item) => (
              <button
                key={item.name}
                type="button"
                className={`template-card${specText === item.yaml ? " selected" : ""}`}
                onClick={() => setSpecText(item.yaml)}
              >
                <strong>{item.name}</strong>
                <span>{item.goal}</span>
              </button>
            ))}
          </aside>
          <label className="spec-slot">
            <span className="slot-label">SwarmSpec</span>
            <span className="slot-hint">Write the spec here, or pick a template to fill it in.</span>
            <textarea
              className="spec-yaml"
              aria-label="SwarmSpec"
              spellCheck={false}
              value={specText}
              placeholder={"apiVersion: termitaria/v1\nkind: SwarmSpec\nname: lab\ngoal: ...\nagents:"}
              onChange={(event) => setSpecText(event.target.value)}
            />
          </label>
        </div>
      )}
      {step === 2 && (
        <div className="knowledge">
          <p className="slot-hint">
            Add notes, links, files, or images. They stay with this project. The graph database is not connected yet.
          </p>
          <label className="name-field">
            <span>Text</span>
            <textarea
              aria-label="Knowledge text"
              value={note}
              placeholder="A note worth keeping"
              onChange={(event) => setNote(event.target.value)}
            />
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
          </label>
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
                addFiles(event.target.files, "file");
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
                addFiles(event.target.files, "image");
                event.target.value = "";
              }}
            />
          </div>
          <ul className="seed-list">
            {seeds.length === 0 ? <li className="slot-hint">Nothing added yet</li> : null}
            {seeds.map((item) => (
              <li key={item.id}>
                <span className="seed-kind">{item.kind}</span>
                <span className="seed-title">{item.title}</span>
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
      )}
    </section>
  );
}
