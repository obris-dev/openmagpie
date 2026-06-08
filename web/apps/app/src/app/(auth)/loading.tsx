// Route-segment loading UI for the (auth) group. Next wraps this segment's
// pages in a Suspense boundary with this as the fallback — which also satisfies
// the useSearchParams() "must be inside Suspense" requirement for home/login/
// signup (all client pages that read search params). Cascades to nested routes.
export { AuthLoading as default } from "./_components/loading/auth-loading";
