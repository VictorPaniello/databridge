// "Hello, {first_name}" - matches the rest of the UI's language. Falls
// back to the email when first_name is unset (every GitHub OAuth signup,
// plus any user who registered before this field existed - see
// UserRead's docstring in the backend's auth.py). Used above the
// "Client records" heading (RecordsPage), not in Layout's header - kept
// here rather than inline so it's not tied to one page if another one
// ends up wanting it too.
export function greeting(user: { email: string; first_name: string | null }): string {
  return user.first_name ? `Hello, ${user.first_name}` : user.email;
}
