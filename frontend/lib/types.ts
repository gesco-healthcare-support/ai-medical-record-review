/** Shape returned by GET /api/users/me (FastAPI-Users UserRead + our required display name). */
export type CurrentUser = {
  id: number;
  email: string;
  name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
};
