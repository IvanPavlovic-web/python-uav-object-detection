import argparse
import sys

from mara.config import ConfigError, load_mission
from mara.decision import DecisionEngine
from mara.logger import EventType, FlightLogger
from mara.mission import (RESULT_COMPLETED, RESULT_STOPPED, RESULT_USER_INTERRUPT,
                          MissionRunner)
from mara.security import SecurityError, load_key
from mara.utils import parse_camera_source, parse_host_port
from mara.vehicle import VehicleError, connect_vehicle, create_simulated_vehicle
from mara.vision import (CameraStream, ObjectDetector, ScriptedDetector,
                         SyntheticFrameSource, VisionError, VisionPipeline)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MARA-1 platforma za autonomnu UAV misiju")
    p.add_argument("--connect", default="127.0.0.1:14550",
                   help="connection string (npr. 127.0.0.1:14550 ili /dev/ttyAMA0)")
    p.add_argument("--baud", type=int, default=57600, help="brzina serijske veze (Pixhawk)")
    p.add_argument("--mission", default="config/default_mission.yaml", help="YAML datoteka misije")
    p.add_argument("--no-vision", action="store_true", help="let bez kamere i YOLO detekcije")
    p.add_argument("--camera", default="0", help="indeks kamere ili putanja do video datoteke")
    p.add_argument("--model", default="yolov8n.pt", help="YOLO model (težine)")
    p.add_argument("--log-dir", default="logs", help="direktorijum za log i snimke")

    sim = p.add_argument_group("simulacija bez ArduPilot SITL-a")
    sim.add_argument("--simulate", action="store_true", help="koristi ugrađeni simulator letjelice")
    sim.add_argument("--time-scale", type=float, default=5.0,
                     help="ubrzanje ugrađenog simulatora (podrazumijevano 5)")
    sim.add_argument("--demo-detection", type=float, metavar="SEKUNDE", default=None,
                     help="TEST: lažna detekcija ciljnog objekta nakon N sekundi (nije YOLO)")

    sec = p.add_argument_group("sigurna telemetrija (TLS 1.3 + HMAC)")
    sec.add_argument("--telemetry", metavar="HOST:PORT", default=None,
                     help="adresa zemaljske stanice; uključuje sigurno slanje događaja")
    sec.add_argument("--ca", default="certs/ca.crt", help="CA certifikat")
    sec.add_argument("--cert", default="certs/client.crt", help="klijentski certifikat")
    sec.add_argument("--key", default="certs/client.key", help="klijentski privatni ključ")
    sec.add_argument("--hmac-key", default="keys/hmac.key", help="datoteka sa HMAC ključem")
    sec.add_argument("--server-name", default="localhost",
                     help="ime iz certifikata servera (za provjeru identiteta)")
    return p


def build_vision(args, mission, logger):
    if args.no_vision:
        logger.log(EventType.INFO, "Vizija isključena (--no-vision).")
        return None
    if args.demo_detection is not None:
        source = SyntheticFrameSource()
        detector = ScriptedDetector(mission.target_object, args.demo_detection)
        logger.log(EventType.INFO, "DEMO: koristi se skriptovana detekcija, ne YOLO.")
    else:
        source = CameraStream(parse_camera_source(args.camera))
        detector = ObjectDetector(args.model)
    pipeline = VisionPipeline(source, detector)
    pipeline.start()
    logger.log(EventType.INFO, "Vizija spremna.")
    return pipeline


def build_telemetry(args, logger):
    if not args.telemetry:
        return None
    from mara.telemetry import TelemetryClient
    host, port = parse_host_port(args.telemetry)
    client = TelemetryClient(host, port, args.ca, args.cert, args.key,
                             load_key(args.hmac_key), server_hostname=args.server_name)
    client.start()
    logger.add_listener(client.send)
    logger.log(EventType.INFO, f"Sigurna telemetrija (TLS 1.3 + HMAC) prema {host}:{port}")
    return client


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logger = FlightLogger(args.log_dir)

    try:
        mission = load_mission(args.mission)
    except ConfigError as exc:
        print(f"Greška u konfiguraciji: {exc}")
        return 2

    vehicle = pipeline = telemetry = None
    exit_code = 0
    try:
        telemetry = build_telemetry(args, logger)
        pipeline = build_vision(args, mission, logger)
        if args.simulate:
            vehicle = create_simulated_vehicle(args.time_scale, logger)
        else:
            vehicle = connect_vehicle(args.connect, args.baud, logger=logger)

        runner = MissionRunner(vehicle, mission, logger, pipeline,
                               DecisionEngine.from_mission(mission),
                               poll_interval_s=0.2 if args.simulate else 1.0)
        result = runner.run()
        print(f"\nRezultat misije: {result}. Log: {logger.path}")
        if result not in (RESULT_COMPLETED, RESULT_STOPPED, RESULT_USER_INTERRUPT):
            exit_code = 1
    except (VehicleError, VisionError, SecurityError, OSError, ValueError) as exc:
        logger.log(EventType.ERROR, str(exc))
        print(f"\nGreška: {exc}")
        if isinstance(exc, VisionError):
            print("Savjet: pokrenite sa --no-vision ili provjerite kameru/model.")
        exit_code = 1
    except KeyboardInterrupt:
        print("\nPrekid (Ctrl+C).")
        exit_code = 130
    finally:
        if pipeline:
            pipeline.stop()
        if telemetry:
            telemetry.stop()
        if vehicle is not None:
            vehicle.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
