// Fixed port and token for the server-side proxy test. The web dev server starts with
// these values; the test starts a fake API on the same port. Not a real credential.
export const PROXY_TEST_PORT = 3199;
export const PROXY_TEST_TOKEN = "e2e-proxy-token-not-a-secret";
export const PROXY_TEST_API_URL = `http://127.0.0.1:${PROXY_TEST_PORT}`;
