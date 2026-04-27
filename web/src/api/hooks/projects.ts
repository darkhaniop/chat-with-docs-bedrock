import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiJson } from "../client";
import type { Page, Project, ProjectSummary } from "../types";

const projectsKey = ["projects"] as const;

// docs/05-api-contracts.md: `GET /projects` is cursor-paginated (default page size 20) and
// `ProjectList` has no "load more" affordance — it renders `data.items` as the complete list.
// Below the default page size this was invisible; once an account has more than 20 projects,
// the un-paginated first page silently hides every project created after that threshold,
// including a project the user just created (found live: the sandbox account's accumulated e2e
// test projects pushed it over 20, and `resilience.spec.ts`'s freshly created project never
// appeared in the sidebar at all). Draining every page here keeps `ProjectList` simple.
export function useProjects() {
  return useQuery({
    queryKey: projectsKey,
    queryFn: async () => {
      const items: ProjectSummary[] = [];
      let cursor: string | null = null;
      do {
        const query = cursor !== null ? `?limit=100&cursor=${encodeURIComponent(cursor)}` : "?limit=100";
        const page: Page<ProjectSummary> = await apiJson<Page<ProjectSummary>>(`/projects${query}`);
        items.push(...page.items);
        cursor = page.nextCursor;
      } while (cursor !== null);
      return { items, nextCursor: null } satisfies Page<ProjectSummary>;
    },
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; description?: string }) =>
      apiJson<Project>("/projects", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectsKey });
    },
  });
}

export function useDeleteProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) =>
      apiJson<{ status: string }>(`/projects/${projectId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectsKey });
    },
  });
}
