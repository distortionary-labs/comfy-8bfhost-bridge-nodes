{ pkgs, lib, config, inputs, ... }:

{
  # gcc builds the test filter. 8bf-host itself is not packaged here: point
  # EIGHTBF_HOST at a build of it.
  packages = with pkgs; [ git gcc ruff ];

  languages.python = {
    enable = true;
    # torch is only needed for tests/test_nodes.py, which skips without it.
    package = pkgs.python312.withPackages (ps: [ ps.pytest ps.numpy ps.torch ]);
  };
}
