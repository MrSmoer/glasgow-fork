# STOLEN FROM HCSCH

{
  inputs = {
    nixpkgs.url = "nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    flake-compat.url = "github:edolstra/flake-compat";
  };
  outputs =
    { nixpkgs, flake-utils, self, ... }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        lib = pkgs.lib;
      in
      {
        packages = rec {
          glasgow = pkgs.glasgow.overridePythonAttrs (previousAttrs: {
            # LIBFX2 = "${pkgs.python3.pkgs.fx2}/share/libfx2";
            YOSYS = "${lib.getBin pkgs.yosys}/bin/yosys";
            ICEPACK = "${lib.getBin pkgs.icestorm}/bin/icepack";
            NEXTPNR_ICE40 = "${lib.getBin pkgs.nextpnr}/bin/nextpnr-ice40";
            version = "0+unstable-1-wire-eeprom";
            src = ./.;
            nativeBuildInputs = previousAttrs.nativeBuildInputs ++ [pkgs.pdm];
            doInstallCheck = false;
          });
          default = glasgow;
        };

        devShell = self.packages.${system}.glasgow;

        # devShell = pkgs.mkShell {
        #   packages = with pkgs; [
        #     # Must come before yosys in this array, so it has precedence
        #     # over the propagatedBuildInput python3 from yosys.
        #     (python3.withPackages (
        #       pypkgs: with pypkgs; [
        #         typing-extensions
        #         amaranth
        #         packaging
        #         platformdirs
        #         fx2
        #         libusb1
        #         pyvcd
        #         aiohttp

        #         unittestCheckHook

        #         black
        #         isort
        #       ]
        #     ))
        #     # libfx2
        #     yosys
        #     icestorm
        #     nextpnr

        #     sdcc
        #   ];
        #   LIBFX2 = "${pkgs.python3.pkgs.fx2}/share/libfx2";
        #   YOSYS = "${lib.getBin pkgs.yosys}/bin/yosys";
        #   ICEPACK = "${lib.getBin pkgs.icestorm}/bin/icepack";
        #   NEXTPNR_ICE40 = "${lib.getBin pkgs.nextpnr}/bin/nextpnr-ice40";
        # };

        formatter = pkgs.nixfmt-rfc-style;
      }
    );
}


#pdm test glasgow.applet.interface.bus1wire_controller.test.Bus1WireControllerAppletTestCase.testreset
#pdm run glasgow run
#pdm run glasgow -vv run 1wire-controller -V 3.3 -f8 --data-pin A1 write1
