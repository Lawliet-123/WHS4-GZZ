import json
import os
import threading
import tkinter as tk
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from tkinter import messagebox, ttk

from camouflage import ensure_bridge_ready, paint_now, shutdown_bridge, stop_paint


CONFIG_PATH = Path(os.environ.get("LOCALAPPDATA", ".")) / "MecchaCamouflageLiteV2" / "config.json"


@dataclass
class CamoConfig:
    game_process_name: str = "PenguinHotel-Win64-Shipping.exe"
    stroke_size_texels: float = 9.0
    coverage_step_texels: float = 4.0
    side_source_max_uv: float = 0.08
    front_back_source_max_uv: float = 0.45
    auto_material: bool = False
    metallic: float = 0.0
    roughness: float = 1.0
    emissive: float = 0.0
    front_region_mode: str = "paint"
    side_region_mode: str = "paint"
    back_region_mode: str = "paint"
    fill_color: str = "#FFFFFF"
    fill_metallic: float = 0.0
    fill_roughness: float = 1.0
    fill_emissive: float = 0.0


def load_config() -> CamoConfig:
    config = CamoConfig()
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            valid = {field.name for field in fields(config)}
            for key, value in saved.items():
                if key in valid:
                    setattr(config, key, value)
    except (OSError, ValueError, TypeError):
        pass
    return config


def save_config(config: CamoConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")


class LiteV2Window(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Meccha Chameleon LiteV2")
        self.geometry("640x560")
        self.minsize(600, 520)
        self.config_data = load_config()
        self.variables = {}
        self.busy = False
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close_app)

    def _build_ui(self):
        settings = ttk.LabelFrame(self, text="원본 Camouflage 설정", padding=12)
        settings.pack(fill="x", padx=12, pady=12)

        rows = (
            ("game_process_name", "게임 프로세스"),
            ("stroke_size_texels", "스트로크 크기"),
            ("coverage_step_texels", "UV 간격"),
            ("side_source_max_uv", "측면 색상 거리"),
            ("front_back_source_max_uv", "전/후면 색상 거리"),
            ("metallic", "Metallic"),
            ("roughness", "Roughness"),
            ("emissive", "Emissive"),
        )
        for row, (key, label) in enumerate(rows):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
            variable = tk.StringVar(value=str(getattr(self.config_data, key)))
            self.variables[key] = variable
            ttk.Entry(settings, textvariable=variable, width=36).grid(row=row, column=1, sticky="ew", pady=3)

        self.variables["auto_material"] = tk.BooleanVar(value=self.config_data.auto_material)
        ttk.Checkbutton(settings, text="원본 자동 Material 속성", variable=self.variables["auto_material"]).grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(6, 2)
        )
        settings.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=12)
        ttk.Button(buttons, text="연결/주입", command=lambda: self.run_action("connect")).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Camouflage 실행", command=lambda: self.run_action("paint")).pack(side="left", padx=6)
        ttk.Button(buttons, text="중지", command=lambda: self.run_action("stop", allow_busy=True)).pack(side="left", padx=6)
        ttk.Button(buttons, text="브리지 종료", command=lambda: self.run_action("shutdown", allow_busy=True)).pack(side="left", padx=6)

        self.status = tk.StringVar(value="준비")
        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=12, pady=(10, 4))
        self.log = tk.Text(self, height=12, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def read_config(self) -> CamoConfig:
        cfg = self.config_data
        cfg.game_process_name = self.variables["game_process_name"].get().strip()
        for key in (
            "stroke_size_texels", "coverage_step_texels", "side_source_max_uv",
            "front_back_source_max_uv", "metallic", "roughness", "emissive",
        ):
            setattr(cfg, key, float(self.variables[key].get().strip()))
        cfg.auto_material = bool(self.variables["auto_material"].get())
        save_config(cfg)
        return cfg

    def append_log(self, value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def run_action(self, action: str, allow_busy: bool = False):
        if self.busy and not allow_busy:
            return
        try:
            cfg = self.read_config()
        except (ValueError, OSError) as exc:
            messagebox.showerror("설정 오류", str(exc))
            return
        if not allow_busy:
            self.busy = True
        self.status.set(f"{action} 실행 중...")

        def worker():
            if action == "connect":
                error = ensure_bridge_ready(cfg.game_process_name)
                result = {"success": not bool(error), "stage": "ready" if not error else "connection_failed", "message": error or "연결 완료"}
            elif action == "paint":
                error = ensure_bridge_ready(cfg.game_process_name)
                result = {"success": False, "stage": "connection_failed", "message": error} if error else paint_now(cfg)
            elif action == "stop":
                result = stop_paint()
            else:
                result = shutdown_bridge()

            def done():
                if not allow_busy:
                    self.busy = False
                self.status.set(str(result.get("stage", "완료")))
                self.append_log(result)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def close_app(self):
        try:
            save_config(self.read_config())
        except (ValueError, OSError):
            pass
        shutdown_bridge()
        self.destroy()


if __name__ == "__main__":
    LiteV2Window().mainloop()
