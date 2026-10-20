# Bring the compose stack back on its own once whatever broke it is fixed.
#
# krg's composeStack unit (krg-infra services/compose-stack.nix) is a oneshot with
# Restart=on-failure, RestartSec=30s and startLimitBurst=5 per hour: after five failed
# starts it parks in `failed` and nothing retries. On 2026-10-20 a full disk did exactly
# that — postgres couldn't start, so `up -d` failed and api/traefik stayed `Created`.
# Once latched, the site (static web included) stays down even after space is freed,
# until someone runs `systemctl reset-failed` + `start` by hand.
#
# This timer does that reset for them, conservatively:
#   - only when the stack unit is `failed` (an operator's deliberate `systemctl stop`
#     leaves it `inactive`, and that is respected);
#   - never while a converge (`reference-manager-selfupdate`) is running;
#   - not while the docker filesystem is still nearly full (< 1 GiB free) — a start
#     there fails the same way and re-pulls whatever images are missing;
#   - at most once an hour. Each recovery re-arms the unit's own 5-starts-per-hour
#     budget, so the worst case on a persistent fault is ~5 attempts/hour — the same
#     bound krg-infra chose to keep a broken stack from hammering the registry.
#
# --no-block: the start can take minutes (pulls, migrations); the unit's own Restart=
# handles the retries, this only lifts the `failed` latch.
{
  config,
  pkgs,
  ...
}: let
  systemctl = "${config.systemd.package}/bin/systemctl";
  stack = "reference-manager.service";
  minFreeMiB = 1024;
  recover = pkgs.writeShellScript "reference-manager-recover" ''
    set -u
    state=$(${systemctl} show ${stack} -p ActiveState --value)
    if [ "$state" != failed ]; then
      exit 0
    fi
    converge=$(${systemctl} show reference-manager-selfupdate.service -p ActiveState --value)
    if [ "$converge" = activating ]; then
      echo "${stack} is failed, but a converge is running; leaving it to the converge"
      exit 0
    fi
    free=$(${pkgs.coreutils}/bin/df --output=avail -BM /var/lib/docker | ${pkgs.coreutils}/bin/tail -1 | ${pkgs.coreutils}/bin/tr -dc 0-9)
    if [ "''${free:-0}" -lt ${toString minFreeMiB} ]; then
      echo "${stack} is failed, but only ''${free:-?} MiB free on /var/lib/docker; not retrying yet" >&2
      exit 0
    fi
    echo "${stack} is failed (''${free} MiB free); resetting and starting it"
    ${systemctl} reset-failed ${stack}
    ${systemctl} start --no-block ${stack}
  '';
in {
  systemd.services.reference-manager-recover = {
    description = "Restart the reference-manager compose stack if it is parked in failed";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${recover}";
    };
  };
  systemd.timers.reference-manager-recover = {
    wantedBy = ["timers.target"];
    timerConfig = {
      # First check well after boot (the stack's own start + retries go first), then
      # hourly. RandomizedDelaySec just avoids lining up with the hourly backup sync.
      OnBootSec = "30min";
      OnUnitActiveSec = "1h";
      RandomizedDelaySec = "5min";
    };
  };
}
