import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiJson } from "../client";
import type { Page, Project, ProjectSummary } from "../types";

const projectsKey = ["projects"] as const;

export function useProjects() {
  return useQuery({
    queryKey: projectsKey,
    queryFn: () => apiJson<Page<ProjectSummary>>("/projects"),
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
