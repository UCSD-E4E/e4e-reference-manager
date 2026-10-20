# Reclaim superseded docker images after each compose-stack converge. Mirrors
# fishsense-services deploy/incus/prune.nix (whose slot filled its disk on 2026-07-16
# with stale image versions: postgres `No space left on device`).
#
# Ordered AFTER `reference-manager.service` (the compose stack) and pulled in whenever
# it starts, so the reclaim runs right after `up -d` has the new containers running
# (their images in use, therefore kept).
#
# Two passes:
#  1. Our own release images (ghcr.io/ucsd-e4e/e4e-reference-manager-*) whose tag is
#     not pinned by the compose file this converge deployed. Age doesn't matter: on
#     2026-10-20 five releases in one day filled the disk, because pass 2 alone keeps
#     anything younger than 72h. `docker rmi` (never forced) refuses an image a
#     container still uses, so this can't break the stack.
#  2. Everything else unused and older than 72h. FILTERED, unlike fishsense's bare
#     `-af`: a bare `-a -f` also removes the image of any container that happens to be
#     STOPPED at that moment, and the next converge then fails "No such image"
#     (krg-infra hit this on krg-prod).
#
# Deliberately `wantedBy` (not `requiredBy`) and no `bindsTo`: a prune failure must
# never fail or block the stack — worst case the disk isn't reclaimed this time.
{
  config,
  pkgs,
  ...
}: let
  docker = "${config.virtualisation.docker.package}/bin/docker";
  grep = "${pkgs.gnugrep}/bin/grep";
  ours = "ghcr\\.io/ucsd-e4e/e4e-reference-manager-";
  pruneSupersededReleases = pkgs.writeShellScript "reference-manager-prune-releases" ''
    set -u
    pinned=$(${grep} -oE '${ours}[a-z0-9-]+:v[0-9]+\.[0-9]+\.[0-9]+' ${./compose.yml})
    if [ -z "$pinned" ]; then
      echo "no pinned release images found in compose.yml; not pruning" >&2
      exit 0
    fi
    stale=$(${docker} images --format '{{.Repository}}:{{.Tag}}' \
      | ${grep} -E '^${ours}' | ${grep} -vxF "$pinned" || true)
    for image in $stale; do
      ${docker} rmi "$image" || true
    done
  '';
in {
  systemd.services.reference-manager-image-prune = {
    description = "Reclaim superseded docker images after the compose-stack converge";
    after = ["reference-manager.service"];
    wantedBy = ["reference-manager.service"];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = [
        "${pruneSupersededReleases}"
        "${docker} image prune -af --filter until=72h"
      ];
    };
  };
}
