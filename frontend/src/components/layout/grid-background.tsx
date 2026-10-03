/** Faint decorative grid that fades out towards the edges. Place inside a `relative isolate` element. */
export function GridBackground() {
  return (
    <div
      aria-hidden
      className="bg-grid pointer-events-none absolute inset-0 -z-10 mask-radial-from-20% mask-radial-to-70%"
    />
  );
}
