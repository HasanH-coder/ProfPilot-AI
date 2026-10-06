export function SiteFooter() {
  return (
    <footer className="public-footer">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-2 px-5 py-5 text-xs text-muted-foreground sm:px-8">
        <span>© {new Date().getFullYear()} ProfPilot AI</span>
        <span>Your academic work, thoughtfully supported.</span>
      </div>
    </footer>
  );
}
