// Server-only bearer helper. Browser bundles get an empty object because the token is
// never exposed with a NEXT_PUBLIC_ name.

export function serverAuthorizationHeader(): Record<string, string> {
	if (typeof window !== "undefined") return {};
	const token = process.env.EZCHAIN_API_TOKEN;
	return token ? { authorization: `Bearer ${token}` } : {};
}
