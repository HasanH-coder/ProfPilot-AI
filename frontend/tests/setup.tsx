// Runs before every test file. The components are tested as they are; only the
// Next.js runtime and the server-only modules (Server Actions) are replaced.

import "vitest-browser-react";
import "@/app/globals.css";

import type { AnchorHTMLAttributes, ImgHTMLAttributes, MouseEvent } from "react";
import { afterEach, vi } from "vitest";

import { resetNavigation } from "./mocks/navigation";

vi.mock("next/navigation", async () => (await import("./mocks/navigation")).navigation);

vi.mock("next/link", async () => {
  const { navigation } = await import("./mocks/navigation");
  function Link({
    href,
    prefetch,
    onClick,
    children,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; prefetch?: boolean | null }) {
    return (
      <a
        href={href}
        data-prefetch={prefetch === undefined || prefetch === null ? "auto" : String(prefetch)}
        onClick={(event: MouseEvent<HTMLAnchorElement>) => {
          event.preventDefault();
          onClick?.(event);
        }}
        {...props}
      >
        {children}
      </a>
    );
  }
  // next/link is CommonJS: __esModule makes `import Link from "next/link"` get the component.
  return { __esModule: true, default: Link, useLinkStatus: () => navigation.linkStatus };
});

vi.mock("next/image", () => ({
  __esModule: true,
  // eslint-disable-next-line @next/next/no-img-element
  default: ({ src, alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) => <img src={String(src)} alt={alt} {...props} />,
}));

// Server Actions: never run in the browser.
vi.mock("@/lib/assessments/actions", () => ({
  createAssessmentDraft: vi.fn(async () => ({ draft: { examProjectId: "draft-1", folder: "prof/draft-1" } })),
  saveAssessmentDraft: vi.fn(async () => ({})),
  deleteAssessment: vi.fn(async () => ({})),
}));
vi.mock("@/lib/documents/actions", () => ({ recordUploadedDocument: vi.fn(), removeDocument: vi.fn() }));
vi.mock("@/lib/courses/actions", () => ({ createCourse: vi.fn(), updateCourse: vi.fn(), deleteCourse: vi.fn() }));
vi.mock("@/lib/auth/actions", () => ({ login: vi.fn(), signup: vi.fn(), logout: vi.fn() }));

// The AI API: each test says what it answers.
vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  apiRequest: vi.fn(),
  apiStream: vi.fn(),
  apiDownload: vi.fn(),
}));

afterEach(() => {
  resetNavigation();
  vi.clearAllMocks();
});
