// The demo build: a simpler ProfPilot for presentations, with one guided path from
// a lecture to a mini quiz. Turned on by NEXT_PUBLIC_DEMO_MODE=true, which Next.js
// reads at build time. Without it, this is the full app.

export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

export const NEW_ASSESSMENT_PATH = "/workspace/assessments/new";

/** Where a signed-in professor lands. */
export const WORKSPACE_HOME = DEMO_MODE ? NEW_ASSESSMENT_PATH : "/workspace";

/** The demo's only duration. Mirrored in backend/app/domain/setup.py. */
export const MINI_QUIZ_MINUTES = 10;

const HIDDEN_PAGES = ["/workspace/courses", "/workspace/sources", "/workspace/preferences"];
const BUILDER_PAGE = /^(\/workspace\/assessments\/[^/]+)\/builder\/?$/;

/** Where the demo sends a page it doesn't show, or null when the page is part of it. */
export function demoRedirect(pathname: string, isSignedIn: boolean): string | null {
  if (pathname === "/") return isSignedIn ? NEW_ASSESSMENT_PATH : "/login";
  if (pathname === "/workspace") return NEW_ASSESSMENT_PATH;
  if (HIDDEN_PAGES.some((page) => pathname === page || pathname.startsWith(`${page}/`))) return NEW_ASSESSMENT_PATH;
  const builder = BUILDER_PAGE.exec(pathname);
  return builder ? `${builder[1]}/exam` : null;
}
