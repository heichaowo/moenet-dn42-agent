"""MoeNet DN42 Agent - BIRD Renderer"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"


class BirdRenderer:
    def __init__(self, template_dir: Path = TEMPLATE_DIR):
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)), trim_blocks=True, lstrip_blocks=True
        )

    def render_peer(self, peer: dict) -> str:
        return self.env.get_template("bird_peer.conf.j2").render(peer=peer)
