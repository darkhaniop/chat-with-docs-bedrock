import { useState } from "react";
import { Button } from "../../components/ui/button";
import { useCreateProject, useDeleteProject, useProjects } from "../../api/hooks/projects";
import { ApiError } from "../../api/errors";

export function ProjectList({
  selectedProjectId,
  onSelect,
}: {
  selectedProjectId: string | null;
  onSelect: (projectId: string) => void;
}) {
  const { data, isLoading, error } = useProjects();
  const createProject = useCreateProject();
  const deleteProject = useDeleteProject();
  const [name, setName] = useState("");

  const submitCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed === "") return;
    createProject.mutate(
      { name: trimmed },
      { onSuccess: (project) => onSelect(project.projectId) },
    );
    setName("");
  };

  return (
    // No self-imposed width here — the caller controls sizing (a resizable `Panel` inside
    // `Workspace`, or a fixed-width wrapper div before a project is selected). A `w-64` used to
    // live on this element directly; nested inside a `Panel` (which sizes via `flex-basis` and
    // relies on `overflow: auto` for anything wider, not on its children's intrinsic width) that
    // just clipped the project name and the "Add"/"Delete" buttons instead of controlling the
    // panel's actual width — confirmed live after the first deploy.
    <div className="flex h-full flex-col gap-3 p-3">
      <form onSubmit={submitCreate} className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New project"
          aria-label="New project name"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
        />
        <Button type="submit" disabled={createProject.isPending || name.trim() === ""}>
          Add
        </Button>
      </form>
      {createProject.error !== null && (
        <p className="text-sm text-red-600">
          {createProject.error instanceof ApiError
            ? createProject.error.message
            : "Failed to create project."}
        </p>
      )}

      {isLoading && <p className="text-sm text-slate-500">Loading projects…</p>}
      {error !== null && <p className="text-sm text-red-600">Failed to load projects.</p>}
      {data !== undefined && data.items.length === 0 && (
        <p className="text-sm text-slate-500">No projects yet.</p>
      )}

      <ul className="flex flex-col gap-1">
        {data?.items.map((project) => (
          <li key={project.projectId} className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => onSelect(project.projectId)}
              className={`min-w-0 flex-1 truncate rounded-md px-2 py-1 text-left text-sm ${
                project.projectId === selectedProjectId
                  ? "bg-slate-900 text-white"
                  : "hover:bg-slate-100"
              }`}
            >
              {project.name}
              <span className="ml-1 text-xs opacity-70">({project.documentCount})</span>
            </button>
            <Button
              variant="outline"
              aria-label={`Delete ${project.name}`}
              onClick={() => deleteProject.mutate(project.projectId)}
            >
              Delete
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
