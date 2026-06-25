HM-KNX
===

ETS-Integration für das Hörmann/Ing.-Budde **HM-KNX** Garagentor-Gateway
(Hersteller 0x4805): die ETS-Produktdatei (knxprod) sowie die optionale
ETS6-DCA-AddIn-Oberfläche, erstellt mit der
[OpenKNX](https://github.com/OpenKNX)-Toolchain.

> Die knxprod ist **dokumentationsorientiert**: Die HM-KNX-Firmware implementiert
> keine Standard-LoadProcedure (Maske 0x0021). ETS programmiert das Gerät darüber
> nicht — die Datei stellt das Gerät lediglich mit korrekten Gruppenobjekt-Namen,
> DPTs und Flags in ETS dar, sodass Gruppenadressen visuell zugewiesen werden
> können.

## Inhalt

- `knxprod/HM-KNX.xml` — selbstständige Quell-XML der ETS-Produktdatei (Catalog,
  Hardware und ApplicationProgram in einer Datei)
- `dca/` — C#-Quellen der ETS6-DCA-AddIn (.NET Framework 4.8)
- `scripts/sideload-dca.ps1` — installiert die AddIn in ETS6
- `scripts/openssl-sha1.cnf` — OpenSSL-Konfiguration für das Signieren unter Linux

## Voraussetzungen

- [OpenKNXproducer](https://github.com/OpenKNX/OpenKNXproducer)
- .NET SDK
- ETS6 — die Signierung der knxprod nutzt die ETS-DLLs (`Knx.Ets.XmlSigning.dll`)

## knxprod bauen

```bash
dotnet OpenKNXproducer.dll knxprod -o HM-KNX.knxprod knxprod/HM-KNX.xml
```

Unter Linux blockiert die System-Krypto-Richtlinie (OpenSSL 3) das von ETS
verwendete RSA-SHA1-Signieren. Die mitgelieferte OpenSSL-Konfiguration aktiviert es
pro Prozess:

```bash
OPENSSL_CONF=scripts/openssl-sha1.cnf \
  dotnet OpenKNXproducer.dll knxprod -o HM-KNX.knxprod knxprod/HM-KNX.xml
```

Unter Windows ist keine zusätzliche Konfiguration nötig. Die erzeugte
`HM-KNX.knxprod` wird in ETS unter *Kataloge → Importieren* eingelesen.

### Hinweis zur Applikations-ID (wichtig für die DCA)

Beim Signieren ersetzt ETS die ID der Applikation durch einen Inhalts-Hash. Für die
mitgelieferte `HM-KNX.xml` lautet sie `M-00FA_A-4805-05-0D9F`; das DCA-Manifest
(`dca/AddInManifest.xml`) ist darauf gebunden. Wird die `HM-KNX.xml` geändert oder
mit einer anderen ETS-Version signiert, ändert sich diese ID — dann muss die
`ApplicationProgram Id` in `dca/AddInManifest.xml` an die ID der gebauten knxprod
angepasst werden.

## DCA bauen & installieren

Die DCA-AddIn ergänzt im ETS einen Konfigurations-Tab für das Gerät.

1. Bauen:

   ```bash
   dotnet build -c Release dca/HM-KNX.Dca.csproj
   ```

2. Installieren (Sideload). ETS prüft die Signatur einer `.etsapp` nur beim
   GUI-Import, nicht beim Lesen eines bereits entpackten AddIn-Ordners. Das Skript
   kopiert DLL + Manifest direkt in das ETS6-AddIns-Verzeichnis:

   ```powershell
   # ETS6 vorher schließen!
   .\scripts\sideload-dca.ps1
   ```

   Erscheint der DCA-Tab nicht, ETS6 schließen,
   `%LocalAppData%\Knx\ETS6\AddInsCache` löschen und ETS6 neu starten.
