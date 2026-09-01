; Inno Setup script for the internal, local-only Windows product.
; The build script supplies TW_STOCK_BUILD_ROOT and TW_STOCK_OUTPUT_DIR.

#ifndef TW_STOCK_BUILD_ROOT
  #error TW_STOCK_BUILD_ROOT is required
#endif
#ifndef TW_STOCK_OUTPUT_DIR
  #error TW_STOCK_OUTPUT_DIR is required
#endif
#ifndef TW_STOCK_APP_VERSION
  #define TW_STOCK_APP_VERSION "1.0.0"
#endif

[Setup]
AppId={{A8B3B5E8-3C15-4E6D-AB9E-9A0D5C4D7E18}
AppName=TW Stock Predictor
AppVersion={#TW_STOCK_APP_VERSION}
AppPublisher=Internal Research Tools
DefaultDirName={localappdata}\Programs\tw-stock-predictor
DefaultGroupName=TW Stock Predictor
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#TW_STOCK_OUTPUT_DIR}
OutputBaseFilename=tw-stock-predictor-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
UninstallDisplayName=TW Stock Predictor

[Files]
Source: "{#TW_STOCK_BUILD_ROOT}\tw-stock-predictor.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#TW_STOCK_BUILD_ROOT}\tw-stock-predictor-server.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\TW Stock Predictor"; Filename: "{app}\tw-stock-predictor.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\TW Stock Predictor"; Filename: "{app}\tw-stock-predictor.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\tw-stock-predictor.exe"; Description: "Launch TW Stock Predictor"; Flags: postinstall nowait skipifsilent
