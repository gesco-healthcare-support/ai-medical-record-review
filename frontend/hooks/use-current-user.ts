import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

/** The signed-in user (GET /api/users/me). Does not retry: a 401 means signed out. */
export function useCurrentUser() {
  return useQuery({
    queryKey: ["current-user"],
    queryFn: () => apiFetch<CurrentUser>("/users/me"),
    retry: false,
    staleTime: 5 * 60_000,
  });
}
