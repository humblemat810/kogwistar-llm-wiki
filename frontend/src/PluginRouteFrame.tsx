type PluginRouteFrameProps = {
  title: string;
  route: string;
  workspaceId: string;
};

/** Mount an installed plugin page without moving its domain UI into the host. */
export function PluginRouteFrame({ title, route, workspaceId }: PluginRouteFrameProps) {
  const separator = route.includes("?") ? "&" : "?";
  const src = `${route}${separator}workspace_id=${encodeURIComponent(workspaceId)}`;
  return (
    <section className="plugin-route-panel" aria-label={title}>
      <iframe title={title} src={src} />
    </section>
  );
}
