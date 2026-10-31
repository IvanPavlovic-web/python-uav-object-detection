# MARA-1

Softverska platforma za autonomnu misiju UAV drona, napisana u Pythonu. Kombinuje upravljanje letom preko DroneKit-a/MAVLink-a, vizuelno prepoznavanje objekata (YOLOv8) i siguran telemetrijski kanal (TLS 1.3 + HMAC) prema zemaljskoj stanici.

## Sadržaj

- [Kako sistem funkcioniše](#kako-sistem-funkcioniše)
- [Arhitektura modula](#arhitektura-modula)
- [Šta je provjereno, a šta nije](#šta-je-provjereno-a-šta-nije)
- [Struktura foldera](#struktura-foldera)
- [Instalacija](#instalacija)
- [ArduPilot SITL (Ubuntu 22.04 ili WSL2)](#ardupilot-sitl-ubuntu-2204-ili-wsl2)
- [Konfiguracija misije (YAML)](#konfiguracija-misije-yaml)
- [Pokretanje aplikacije](#pokretanje-aplikacije)
- [Sigurna telemetrija (TLS 1.3 + HMAC)](#sigurna-telemetrija-tls-13--hmac)
- [Tello klijent (nezavisan modul)](#tello-klijent-nezavisan-modul)
- [Automatski testovi](#automatski-testovi)
- [Najčešće greške i rješenja](#najčešće-greške-i-rješenja)
- [Sigurnosna napomena](#sigurnosna-napomena)

## Kako sistem funkcioniše

Jedno pokretanje `main.py` prolazi kroz sljedeće korake, redom:

1. **Učitavanje misije.** `mara/config.py` učita YAML datoteku, provjeri da su sva obavezna polja prisutna i u dozvoljenom opsegu (visina, koordinate, pragovi) i pretvori je u nepromjenjivi (`frozen`) objekat `Mission`. Ako nešto nedostaje ili je van opsega, program se zaustavlja prije bilo kakvog pokušaja leta (izlazni kod 2).
2. **(Opcionalno) uspostavljanje sigurne telemetrije.** Ako je zadato `--telemetry HOST:PORT`, pokreće se `TelemetryClient` koji u pozadinskoj niti otvara TLS 1.3 vezu prema zemaljskoj stanici i potpisuje svaki događaj HMAC-om prije slanja. Ovo se radi prije leta, ali ne blokira let ako veza ne uspije.
3. **(Opcionalno) pokretanje vizije.** Ako `--no-vision` nije zadato, pokreće se `VisionPipeline`: posebna nit čita kameru/video (`CameraStream`), a glavna petlja misije periodično poziva YOLO detektor (`ObjectDetector`) nad posljednjim dostupnim frejmom. Ako kamera ili YOLO model nisu dostupni, program se zaustavlja prije polijetanja — namjerno, jer je sigurnije ne poletjeti nego letjeti "slijepo".
4. **Povezivanje na letjelicu.** Sa `--simulate` koristi se ugrađeni simulator (`mara/simulated.py`, bez ArduPilot-a, samo za provjeru logike); bez te opcije, `connect_vehicle()` se povezuje preko DroneKit-a na stvarni SITL ili fizički autopilot (MAVLink connection string ili serijski port).
5. **Izvršavanje misije** (`MissionRunner.run()`):
   - `arm_and_takeoff()` čeka da letjelica bude spremna za armiranje (`is_armable`), postavlja režim GUIDED, armira motore i poliježe do zadane visine.
   - Za svaku waypoint tačku iz misije, `fly_to_waypoint()` šalje letjelicu ka cilju (`simple_goto`) i u petlji provjerava udaljenost (Haversine formula + visinska razlika) dok ne uđe u `waypoint_radius_m`, dok ne istekne `waypoint_timeout_s`, ili dok detekcija ne zatraži prekid.
   - Na svakom koraku petlje (`on_tick`) poziva se `MissionRunner._tick()`, koji pita `VisionPipeline` za novi rezultat detekcije i prosljeđuje ga u `DecisionEngine`.
6. **Potvrda detekcije.** `DecisionEngine.evaluate()` prihvata samo detekcije ciljne klase (`target_object`) sa pouzdanošću iznad `confidence_threshold`, i zahtijeva da se objekat pojavi u `required_consecutive_frames` uzastopnih poziva prije nego je potvrđen (filtrira pojedinačne lažne detekcije). Nakon potvrde ulazi u `cooldown` period (`detection_cooldown_seconds`) tokom kojeg se nove detekcije ignorišu.
7. **Reakcija na potvrđenu detekciju.** Letjelica prelazi u zadati režim (`detection_action`, podrazumijevano LOITER), snima sliku sa ucrtanim okvirom detekcije (`save_snapshot`) i upisuje događaj u CSV log. Ako je `stop_on_detection: true`, misija se odmah završava (rezultat `STOPPED_ON_DETECTION`). Ako je `false`, letjelica lebdi `resume_hold_seconds`, pa se vraća u GUIDED i nastavlja ka istoj tački.
8. **Kraj misije.** Nakon posljednje tačke, letjelica prelazi u `end_action` (podrazumijevano RTL) i misija se završava sa rezultatom `COMPLETED`. Na grešci u komunikaciji sa letjelicom (`VehicleError`) ili bilo kojoj neočekivanoj izuzetku, poziva se failsafe (RTL); na `Ctrl+C`, letjelica prelazi u LOITER.
9. **Logovanje tokom cijelog toka.** Svaki događaj (`FlightLogger.log()`) istovremeno: upisuje red u CSV, ispisuje na konzolu i prosljeđuje svim registrovanim slušaocima — ako je telemetrija aktivna, `TelemetryClient.send()` je jedan od tih slušalaca, pa svaki CSV red odmah postaje i telemetrijska poruka.
10. **Zatvaranje.** Bez obzira na ishod, u `finally` bloku se zaustavljaju (tim redom) vizija, telemetrija i veza sa letjelicom.

Na strani zemaljske stanice (odvojen proces, `python -m mara.groundstation`): `GroundStation` prihvata TLS 1.3 konekcije sa obaveznim klijentskim certifikatom, za svaku vezu provjerava HMAC potpis, vremenski prozor, ponavljanje (nonce) i redni broj svake poruke, pa prihvaćene događaje upisuje u JSONL datoteku i ispisuje na konzolu.

## Arhitektura modula

| Modul | Odgovornost |
|---|---|
| `mara/config.py` | Učitavanje i validacija YAML misije → `Mission`/`Waypoint` (dataklase) |
| `mara/vehicle.py` | Veza sa DroneKit-om (ili ugrađenim simulatorom), promjena režima, armiranje i polijetanje |
| `mara/simulated.py` | Ugrađeni simulator letjelice (kinematika leta bez ArduPilot-a) — za `--simulate` i testove |
| `mara/mission.py` | `MissionRunner` — glavna petlja: let kroz waypointe, poziv detekcije na svakom koraku, failsafe |
| `mara/vision.py` | `CameraStream` (čitanje kamere/videa u posebnoj niti), `ObjectDetector` (YOLOv8 preko Ultralytics), `VisionPipeline` (ograničava učestalost obrade), `ScriptedDetector`/`SyntheticFrameSource` (DEMO, bez stvarne detekcije) |
| `mara/decision.py` | `DecisionEngine` — potvrda detekcije kroz više uzastopnih frejmova, cooldown |
| `mara/logger.py` | `FlightLogger` — CSV log + mehanizam slušalaca (listener) za telemetriju |
| `mara/security.py` | HMAC potpisivanje/provjera poruka (`MessageSigner`/`MessageVerifier`), zaštita od ponavljanja (`ReplayCache`), upravljanje ključem |
| `mara/telemetry.py` | `TelemetryClient` — TLS 1.3 klijent, neblokirajući red čekanja, automatsko ponovno povezivanje |
| `mara/groundstation.py` | `GroundStation` — TLS 1.3 server, obrada i upis primljenih poruka, CLI (`python -m mara.groundstation`) |
| `mara/utils.py` | Haversine udaljenost, parsiranje adrese/kamere, UTC vremenska oznaka |
| `mara/tello.py` | Samostalan UDP klijent za DJI Tello — **nije** povezan sa `MissionRunner`-om ni ostatkom sistema |
| `main.py` | Sastavlja sve komponente prema CLI argumentima i pokreće misiju |

## Šta je provjereno, a šta nije

Provjereno automatskim testovima (`pytest`, bez drona i bez kamere): učitavanje i validacija misije, računanje udaljenosti, logika odlučivanja, logovanje, kompletna misija na ugrađenom simulatoru (polijetanje, waypointi, detekcija, nastavak, timeout i RTL), HMAC potpisivanje, zaštita od ponavljanja poruka i TLS 1.3 kanal (odbijanje tuđeg certifikata, TLS 1.2 klijenta, običnog TCP-a, pogrešnog ključa i izmijenjenih poruka) te sigurnosni scenariji iz testnog paketa.

Zavisi od vašeg okruženja i morate ga sami provjeriti: povezivanje DroneKit-a sa ArduPilot SITL-om, YOLO detekcija na stvarnoj kameri i let na fizičkom Pixhawk-u. Kod za te dijelove prati dokumentaciju biblioteka, ali ovdje nije izvršen nad stvarnim SITL-om ni stvarnom kamerom. Fizičko testiranje nije provedeno.

## Struktura foldera

```
MARA-1/
├── main.py                     ulazna tačka (CLI argumenti, sastavljanje komponenti)
├── requirements.txt            zavisnosti za rad aplikacije
├── requirements-dev.txt        zavisnosti + pytest
├── config/
│   └── default_mission.yaml    podrazumijevana misija (SITL koordinate)
├── mara/
│   ├── config.py                učitavanje i validacija YAML misije
│   ├── vehicle.py               povezivanje, režimi leta, arm_and_takeoff
│   ├── simulated.py              ugrađeni simulator letjelice
│   ├── mission.py                let kroz waypointe, integracija detekcije
│   ├── vision.py                 kamera (nit), YOLO detektor, snimak slike
│   ├── decision.py               potvrda detekcije (uzastopni frejmovi, cooldown)
│   ├── logger.py                 CSV logovanje događaja
│   ├── security.py               HMAC potpis, nonce, vremenski prozor, redni broj
│   ├── telemetry.py              TLS 1.3 klijent (dron), red čekanja, ponovno spajanje
│   ├── groundstation.py          TLS 1.3 server (zemaljska stanica)
│   ├── tello.py                  samostalan UDP klijent za DJI Tello
│   └── utils.py                  haversine udaljenost, parsiranje adresa
├── scripts/
│   └── generate_certs.sh        pravi testnu CA, serverski i klijentski certifikat
├── tests/                        automatski testovi (pytest)
├── logs/                         CSV log, snimci detekcije, primljena telemetrija (podrazumijevano)
└── certs/  keys/                 certifikati i HMAC ključ (ne objavljivati)
```

## Instalacija

```bash
python3.10 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Napomena za DroneKit i Python 3.10 ili noviji: DroneKit 2.9.2 koristi `collections.MutableMapping`, koji je u Pythonu 3.10 premješten u `collections.abc`, a uz to koristi paket `future` koji sam ne instalira (zato je `future` u `requirements.txt`). Funkcija `_apply_dronekit_compat()` u `mara/vehicle.py` automatski dodaje stara imena u modul `collections` prije uvoza DroneKit-a, pa nije potrebno ručno mijenjati biblioteku. Ako ipak dobijete grešku, koristite Python 3.9 ili 3.10 u virtualnom okruženju.

Ultralytics pri prvom pokretanju sam preuzima težine `yolov8n.pt` (potreban je internet).

## ArduPilot SITL (Ubuntu 22.04 ili WSL2)

```bash
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot.git
cd ardupilot
Tools/environment_install/install-prereqs-ubuntu.sh -y
. ~/.profile
cd ArduCopter
sim_vehicle.py -w                      # samo prvi put: briše EEPROM i učitava parametre
sim_vehicle.py --console --map --out=127.0.0.1:14550
```

`sim_vehicle.py` pokreće MAVProxy, koji prosljeđuje MAVLink na UDP port 14550. Sačekajte da se u konzoli pojavi poruka o GPS fiksu i EKF-u (to može potrajati do minut), jer dok autopilot nije spreman `vehicle.is_armable` ostaje `False`. Po želji se simulacija može ubrzati opcijom `--speedup 2`. Za vizuelnu potvrdu leta može se spojiti QGroundControl, koji sam prima MAVLink na istom portu.

Ako se `main.py` pokreće unutar istog WSL-a kao i SITL, koristi se `127.0.0.1:14550`. Ako se pokreće u Windows-u dok je SITL u WSL-u, potrebna je WSL-ova mrežna adresa umjesto `127.0.0.1` (`wsl hostname -I`) ili `--out=0.0.0.0:14550` na strani SITL-a.

## Konfiguracija misije (YAML)

Misija se opisuje YAML datotekom (podrazumijevano `config/default_mission.yaml`) i validira funkcijom `load_mission()` u `mara/config.py`. Polja:

| Polje | Obavezno | Podrazumijevano | Opseg/pravilo |
|---|---|---|---|
| `mission_name` | ne | `"MARA-1 misija"` | tekst |
| `takeoff_altitude` | **da** | — | broj, 1.0–120.0 (metara) |
| `waypoints` | **da** | — | neprazna lista; svaka tačka ima `name`, `lat` (-90..90), `lon` (-180..180), `alt` (1.0–120.0) |
| `target_object` | ne | `person` | naziv klase iz COCO skupa (person, car, bicycle ...) |
| `confidence_threshold` | ne | `0.60` | 0.0–1.0 |
| `required_consecutive_frames` | ne | `3` | cio broj ≥ 1 |
| `detection_action` | ne | `LOITER` | `LOITER`, `RTL` ili `LAND` |
| `stop_on_detection` | ne | `true` | `true`/`false`; ako je `false`, `detection_action` mora biti `LOITER` |
| `resume_hold_seconds` | ne | `5.0` | ≥ 0 (koliko lebdjeti prije nastavka, ako `stop_on_detection: false`) |
| `detection_cooldown_seconds` | ne | `30.0` | ≥ 0 (pauza prije sljedeće potvrde detekcije) |
| `waypoint_radius_m` | ne | `2.0` | ≥ 0.5 (tačka je "dostignuta" unutar ovog radijusa) |
| `waypoint_timeout_s` | ne | `120.0` | ≥ 5.0 (ograničenje trajanja leta do jedne tačke) |
| `end_action` | ne | `RTL` | `RTL`, `LAND` ili `LOITER` (šta raditi nakon zadnje tačke) |

Neispravna ili nepotpuna misija diže `ConfigError` i program se zaustavlja prije bilo kakvog pokušaja leta (izlazni kod 2).

## Pokretanje aplikacije

### Osnovni argumenti

| Argument | Podrazumijevano | Opis |
|---|---|---|
| `--connect` | `127.0.0.1:14550` | MAVLink adresa (SITL) ili serijski port (`/dev/ttyAMA0`) |
| `--baud` | `57600` | brzina serijske veze (fizički Pixhawk) |
| `--mission` | `config/default_mission.yaml` | YAML datoteka misije |
| `--no-vision` | isključeno | let bez kamere i YOLO detekcije |
| `--camera` | `0` | indeks kamere ili putanja do video datoteke |
| `--model` | `yolov8n.pt` | YOLO model (težine) |
| `--log-dir` | `logs` | direktorijum za CSV log i snimke detekcije |

### Simulacija bez ArduPilot SITL-a

| Argument | Podrazumijevano | Opis |
|---|---|---|
| `--simulate` | isključeno | koristi ugrađeni simulator letjelice umjesto DroneKit-a |
| `--time-scale` | `5.0` | ubrzanje ugrađenog simulatora |
| `--demo-detection SEKUNDE` | isključeno | **DEMO** — lažna detekcija ciljnog objekta nakon N sekundi, nije YOLO |

### Sigurna telemetrija

| Argument | Podrazumijevano | Opis |
|---|---|---|
| `--telemetry HOST:PORT` | isključeno | adresa zemaljske stanice; uključuje sigurno slanje događaja |
| `--ca` | `certs/ca.crt` | CA certifikat |
| `--cert` | `certs/client.crt` | klijentski certifikat |
| `--key` | `certs/client.key` | klijentski privatni ključ |
| `--hmac-key` | `keys/hmac.key` | datoteka sa HMAC ključem |
| `--server-name` | `localhost` | ime iz certifikata servera (provjera identiteta) |

### Primjeri

```bash
# bez vizije (samo let)
python main.py --connect 127.0.0.1:14550 --no-vision

# sa YOLO detekcijom (web kamera ili video datoteka)
python main.py --connect 127.0.0.1:14550 --mission config/default_mission.yaml \
               --camera 0 --model yolov8n.pt
python main.py --connect 127.0.0.1:14550 --camera snimak.mp4

# fizički Pixhawk preko UART-a Raspberry Pi računara
python main.py --connect /dev/ttyAMA0 --baud 57600

# brza provjera logike bez SITL-a, bez kamere i bez YOLO-a
python main.py --simulate --no-vision
```

Ako je vizija uključena, a kamera ili model nisu dostupni, program se zaustavlja prije polijetanja uz jasnu poruku. Opcija `--no-vision` zaobilazi taj zahtjev.

Provjera logike detekcije bez kamere i bez YOLO-a (lažna detekcija nakon N sekundi — ovo **nije** stvarna detekcija i ne smije se prikazivati kao rezultat YOLO modela):

```bash
python main.py --simulate --demo-detection 6
```

## Sigurna telemetrija (TLS 1.3 + HMAC)

```bash
bash scripts/generate_certs.sh certs        # CA, server (localhost), klijent (MARA-1)
python -m mara.security genkey keys/hmac.key

# terminal 1: zemaljska stanica
python -m mara.groundstation --port 8765

# terminal 2: dron (ili simulacija) šalje događaje
python main.py --simulate --demo-detection 6 --telemetry 127.0.0.1:8765
```

**Transportni sloj.** `TelemetryClient` i `GroundStation` komuniciraju isključivo preko TLS 1.3 (`minimum_version = TLSVersion.TLSv1_3`) sa obostranom autentifikacijom (mTLS): server zahtijeva klijentski certifikat potpisan istom CA (`CERT_REQUIRED`), a klijent provjerava ime servera iz certifikata (`check_hostname`). Starije verzije TLS-a, klijenti bez certifikata i certifikati druge CA se odbijaju već u TLS rukovanju, prije nego se razmijeni ijedna poruka.

**Format poruke.** Svaka poruka je jedan red JSON-a (`\n`-razdvojen), sa poljima:

```json
{"v": 1, "sid": "MARA-1", "seq": 42, "ts": 1731000000.123, "nonce": "a1b2c3...", "payload": {...}, "mac": "..."}
```

`MessageSigner.sign()` na dronu poveća redni broj (`seq`), doda trenutni UTC timestamp (`ts`) i nasumični `nonce`, pa potpiše cijelu poruku (bez polja `mac`) HMAC-SHA256 ključem iz `keys/hmac.key`. `MessageVerifier.verify()` na stanici provjerava, tim redom: strukturu i verziju protokola, HMAC potpis (`hmac.compare_digest`, otporno na timing napad), vremenski prozor (`|sada − ts| ≤ max_skew_s`, podrazumijevano 30 s), da `nonce` nije već viđen (`ReplayCache`, zaštita od ponavljanja snimljene poruke) i da je `seq` strogo veći od posljednjeg primljenog za isti `sid` (zaštita od ponavljanja starijih poruka u nizu). Prva greška u ovom nizu odbacuje poruku uz jasan razlog u logu stanice.

Zemaljska stanica ispisuje primljene događaje na konzolu i upisuje ih u JSONL datoteku (podrazumijevano `logs/ground_station.jsonl`, CLI opcija `--out`). Stanica broji `accepted`/`rejected`/`handshake_failed`/`connections` u `GroundStation.stats`. Ako mreža nije dostupna, `TelemetryClient` čuva događaje u ograničenom redu čekanja (podrazumijevano 256) i pokušava ponovo sa eksponencijalnim zastojem (do 10 s); kada se red napuni, najnoviji događaji se odbacuju (`dropped`), ali **misija nikad ne čeka** telemetriju — let nastavlja bez obzira na stanje mreže.

Za pravi sistem: CA ključ (`ca.key`) čuvajte van drona, HMAC ključ nikada ne objavljujte u repozitorijumu, a satove drona i zemaljske stanice sinhronizujte (NTP), jer se poruke starije od `max_skew_s` (podrazumijevano 30 s) odbacuju.

## Tello klijent (nezavisan modul)

`mara/tello.py` je samostalan UDP klijent za DJI Tello (SDK komande preko porta 8889, stanje preko 8890, video preko 11111). **Nije povezan** sa `MissionRunner`-om, `mara/config.py` misijama ni ostatkom sistema — koristi se nezavisno (`python -m mara.tello` za brzu provjeru baterije) i pokriven je sopstvenim testovima (`tests/test_tello.py`), bez stvarnog Tello uređaja (mrežna komunikacija je simulirana lažnim UDP parom u testovima).

## Automatski testovi

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Dio testova (TLS kanal i sigurnosni scenariji napada, u `test_tls_channel.py` i `test_napadi.py`) zahtijeva da `openssl` i `bash` budu dostupni u okruženju — koriste `scripts/generate_certs.sh` da naprave prave testne sertifikate. Na Windowsu u PowerShell-u, prije pokretanja testova, dodajte Git-ove alate na PATH:

```powershell
$env:PATH = "C:\Program Files\Git\usr\bin;C:\Program Files\Git\bin;" + $env:PATH
python -m pytest -q
```

Bez ovoga se ti testovi preskaču (ne padaju).

## Najčešće greške i rješenja

| Greška | Uzrok i rješenje |
|---|---|
| `Povezivanje nije uspjelo` / timeout | SITL nije pokrenut ili nema `--out=127.0.0.1:14550`. Pokrenite SITL prvi i provjerite port. |
| Dugo čekanje na `is_armable` | EKF i GPS još nisu spremni. Sačekajte poruke u MAVProxy konzoli. |
| `Režim GUIDED nije potvrđen` | Autopilot odbija promjenu režima. Provjerite poruke u MAVProxy konzoli. |
| `AttributeError: collections.MutableMapping` | DroneKit na Pythonu 3.10+. Shim u `mara/vehicle.py` je ugrađen; provjerite da li je `mara.vehicle` uvezen prije `dronekit`-a. |
| `No module named 'past'` | Nedostaje paket `future`: `pip install future`. |
| `Kamera/video nije dostupan` | Pogrešan indeks (`--camera 1`), kamera zauzeta ili nema dozvola. Probajte `--no-vision`. |
| YOLO model se ne učitava | Nema interneta za prvo preuzimanje težina. Preuzmite `yolov8n.pt` ručno u folder projekta. |
| Spor YOLO na Raspberry Pi | Koristite `yolov8n.pt` i manju rezoluciju; brzinu provjerite na svom uređaju. |
| TLS: `certificate verify failed` | Pogrešna CA ili `--server-name` ne odgovara imenu u certifikatu servera. |
| TLS: server odbacuje vezu | Klijentski certifikat nije od iste CA ili klijent ne podržava TLS 1.3. |
| `Odbačena poruka: Potpis poruke nije ispravan` | Različiti HMAC ključevi na dronu i stanici. |
| `izvan dozvoljenog vremenskog prozora` | Satovi nisu sinhronizovani (NTP) ili je poruka starija od `max_skew_s`. |
| 20 testova preskočeno pri `pytest` | `openssl`/`bash` nisu na PATH-u — vidi odjeljak [Automatski testovi](#automatski-testovi). |

## Sigurnosna napomena

Fizički let izvodite isključivo u sigurnom i kontrolisanom okruženju, na otvorenom prostoru, sa pilotom spremnim da preuzme ručnu kontrolu i u skladu sa važećim propisima o upotrebi bespilotnih letjelica u vašoj državi. Prije bilo kakvog autonomnog leta na stvarnom hardveru obavezno uradite kalibraciju senzora, provjeru GPS-a i sigurnosnih parametara (failsafe, RTL visina) u QGroundControl-u, a kod prvo testirajte u SITL-u. Pri letu bez propelera provjerite logiku na stolu.

## Licenca i ograničenja

Edukativni prototip. Nije namijenjen za komercijalnu ni sigurnosno kritičnu upotrebu. YOLO detekcija nije savršena: lažne i propuštene detekcije su moguće, zato sistem zahtijeva više uzastopnih potvrda i koristi konzervativnu reakciju (LOITER).
