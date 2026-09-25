import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Link, Outlet, RouterProvider } from "react-router";
import { ProjectList } from "./pages/ProjectList";
import { ProjectPage } from "./pages/ProjectPage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
});

function Layout() {
  return (
    <>
      <header className="app-bar">
        <Link to="/" className="brand">
          audio2chordpro
        </Link>
      </header>
      <Outlet />
    </>
  );
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <ProjectList /> },
      { path: "/p/:id", element: <ProjectPage /> },
      { path: "*", element: <main className="page">ページがありません。<Link to="/">一覧へ</Link></main> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
