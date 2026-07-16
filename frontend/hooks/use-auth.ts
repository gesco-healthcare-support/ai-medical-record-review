import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as authApi from "@/lib/auth-api";

/** Sign in, then refresh the cached current user so guards/menus update. */
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { email: string; password: string }) => authApi.login(vars.email, vars.password),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["current-user"] }),
  });
}

/** Register a new account. The backend does not start a session on register, so callers
 *  follow a successful register with a login. */
export function useRegister() {
  return useMutation({ mutationFn: authApi.register });
}

export function useForgotPassword() {
  return useMutation({ mutationFn: (email: string) => authApi.forgotPassword(email) });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: (vars: { token: string; password: string }) =>
      authApi.resetPassword(vars.token, vars.password),
  });
}
