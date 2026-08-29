[Setup]
AppId={{FAD5F563-8D91-4D92-9A92-0FD0BE8C2C9B}}
AppName=Telegram Exporter
AppVersion=2.2.0
AppPublisher=Telegram Exporter
DefaultDirName={autopf}\Telegram Exporter
DefaultGroupName=Telegram Exporter
DisableProgramGroupPage=yes
OutputBaseFilename=TelegramExporterSetup
OutputDir=..\dist
Compression=lzma
SolidCompression=yes
WizardStyle=modern

#define IconFile "..\icons\app.ico"
#ifexist IconFile
SetupIconFile={#IconFile}
#endif

UninstallDisplayIcon={app}\TelegramExporter.exe

[Files]
Source: "..\dist\TelegramExporter.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Telegram Exporter"; Filename: "{app}\TelegramExporter.exe"
Name: "{commondesktop}\Telegram Exporter"; Filename: "{app}\TelegramExporter.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"
