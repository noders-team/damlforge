{
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = nixpkgs.lib.genAttrs systems;
      pythons = [ "python311" "python312" "python313" ];
      shellFor = pkgs: python: pkgs.mkShell {
        packages = [
          pkgs.${python}
          pkgs.git
          pkgs.gnumake
          (import ./dpm.nix { inherit pkgs; })
        ];
        LD_LIBRARY_PATH = "${pkgs.stdenv.cc.cc.lib}/lib";
      };
    in
    {
      devShells = forAll (system:
        let pkgs = import nixpkgs { inherit system; };
        in
        { default = shellFor pkgs "python313"; }
        // nixpkgs.lib.genAttrs pythons (python: shellFor pkgs python));
    };
}
