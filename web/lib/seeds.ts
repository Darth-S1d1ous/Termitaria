export type SeedKind = "text" | "link" | "file" | "image";

export type Seed = {
  id: string;
  kind: SeedKind;
  title: string;
  body: string;
};

const key = (projectId: string) => `termitaria.seeds.${projectId}`;

export function loadSeeds(projectId: string): Seed[] {
  try {
    const raw = localStorage.getItem(key(projectId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Seed[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveSeeds(projectId: string, seeds: Seed[]) {
  localStorage.setItem(key(projectId), JSON.stringify(seeds));
}

export function deleteSeeds(projectId: string) {
  localStorage.removeItem(key(projectId));
}
