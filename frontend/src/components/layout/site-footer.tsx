export function SiteFooter() {
  return (
    <footer className="border-t">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center px-6 text-sm text-muted-foreground">
        © {new Date().getFullYear()} ProfPilot AI
      </div>
    </footer>
  );
}
