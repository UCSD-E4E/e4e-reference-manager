# Reclaim superseded docker images after each compose-stack converge. Mirrors
# fishsense-services deploy/incus/prune.nix (whose slot filled its disk on 2026-07-16
# with stale image versions: postgres `No space left on device`).
#
# Ordered AFTER `reference-manager.service` (the compose stack) and pulled in whenever
# it starts — but it only RUNS when that start succeeded (ExecCondition below). On a
# failed start (`up -d` failing after the ExecStop's `down`) there may be no containers
# at all, so nothing would mark grobid/ollama/postgres/traefik as in use; a prune then
# would throw away ~40 GB that the next start has to re-download (on a full disk, it
# can't).
#
# Two passes, neither of which ever removes an image named in compose.yml:
#  1. Our own release images (ghcr.io/ucsd-e4e/e4e-reference-manager-*) whose tag is
#     not pinned by the compose file this converge deployed. Age doesn't matter: on
#     2026-10-20 five releases in one day filled the disk, because pass 2 alone keeps
#     anything younger than 72h.
#  2. Every other tagged image older than 72h that compose.yml does not name (a
#     superseded third-party pin, an image left from a manual run), then dangling
#     layers. NOT `docker image prune -af`: that keeps only images some container
#     (running OR stopped — verified on docker 29.8) references, so it is safe only
#     while the containers exist; with the stack `down` it deletes every pinned image.
# `docker rmi` is never forced, so it also refuses an image a container still uses.
#
# The krg fleet's daily `docker-image-prune` timer (krg-infra modules/docker.nix,
# `image prune -af --filter until=168h`) has the same hole, so it is pointed at pass 2
# too (below).
#
# Deliberately `wantedBy` (not `requiredBy`) and no `bindsTo`: a prune failure must
# never fail or block the stack — worst case the disk isn't reclaimed this time.
{
  config,
  lib,
  pkgs,
  ...
}: let
  docker = "${config.virtualisation.docker.package}/bin/docker";
  grep = "${pkgs.gnugrep}/bin/grep";
  sed = "${pkgs.gnused}/bin/sed";
  ours = "ghcr\\.io/ucsd-e4e/e4e-reference-manager-";
  compose = ./compose.yml;

  pruneSupersededReleases = pkgs.writeShellScript "reference-manager-prune-releases" ''
    set -u
    pinned=$(${grep} -oE '${ours}[a-z0-9-]+:v[0-9]+\.[0-9]+\.[0-9]+' ${compose})
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

  # $1 = age (docker `until` filter, e.g. 72h). Docker lists Hub images without the
  # registry prefix (`traefik:3.7.8`, `grobid/grobid:0.8.1`), so both sides are
  # normalised the same way before comparing.
  pruneUnnamedImages = pkgs.writeShellScript "reference-manager-prune-unnamed" ''
    set -u
    age=$1
    norm() { ${sed} -E 's#^docker\.io/(library/)?##'; }
    named=$(${grep} -oE '^[[:space:]]+image:[[:space:]]+[^[:space:]#]+' ${compose} \
      | ${sed} -E 's/^[[:space:]]+image:[[:space:]]+//; s/"//g' | norm)
    if [ -z "$named" ]; then
      echo "no images found in compose.yml; not pruning" >&2
      exit 0
    fi
    old=$(${docker} images --filter dangling=false --filter "until=$age" \
      --format '{{.Repository}}:{{.Tag}}' | ${grep} -v ':<none>$' | norm || true)
    for image in $old; do
      if printf '%s\n' "$named" | ${grep} -qxF "$image"; then
        continue
      fi
      ${docker} rmi "$image" || true
    done
    # Untagged layers only (no -a): nothing compose names can be dangling.
    ${docker} image prune -f --filter "until=$age"
  '';
in {
  systemd.services.reference-manager-image-prune = {
    description = "Reclaim superseded docker images after the compose-stack converge";
    after = ["reference-manager.service"];
    wantedBy = ["reference-manager.service"];
    serviceConfig = {
      Type = "oneshot";
      # Skip (not fail) unless the stack's start succeeded: the unit is a oneshot with
      # RemainAfterExit, so it is `active` only after `up -d` exited 0. A non-zero
      # ExecCondition (is-active exits 3) leaves this unit inactive, never `failed`.
      ExecCondition = "${config.systemd.package}/bin/systemctl is-active --quiet reference-manager.service";
      ExecStart = [
        "${pruneSupersededReleases}"
        "${pruneUnnamedImages} 72h"
      ];
    };
  };

  # The fleet's daily prune, made compose-aware (same age it had). It runs regardless
  # of the stack's state: with compose's images protected by name, that is safe.
  systemd.services.docker-image-prune.serviceConfig.ExecStart =
    lib.mkForce "${pruneUnnamedImages} 168h";
}
