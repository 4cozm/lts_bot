from __future__ import annotations

import asyncio
import logging
import signal
import sys
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 프로젝트 루트(backend 상위)의 .env 로드
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from audio_handler import AudioHandler
from live_session_manager import LiveSessionManager
from llm_agent import LLMAgent
from sound_player import play_sound
from tts_handler import TTSHandler
from websocket_server import WebSocketBridge
from window_manager import WindowManager


class AssistantMode(str, Enum):
    NORMAL_MODE = "NORMAL_MODE"
    TRANSLATION_MODE = "TRANSLATION_MODE"


class VoiceAssistantApp:
    def __init__(self) -> None:
        self.tts = TTSHandler()
        self.live_session = LiveSessionManager(on_error=self._on_audio_error)
        self.audio = AudioHandler(
            on_error=self._on_audio_error,
            is_output_locked=self.tts.is_output_locked,
            session_manager=self.live_session,
            on_gemini_invoked=lambda: play_sound("copy.mp3"),
        )
        self.llm = LLMAgent()
        try:
            self.windows = WindowManager()
        except Exception as exc:
            self.windows = None
            logger.exception("창 제어 기능 비활성화: %s", exc)
            play_sound("error.mp3")
        self.ws_bridge = WebSocketBridge()

        self.mode = AssistantMode.NORMAL_MODE
        self.translation_target_lang = "en"
        self._running = True

    def _on_audio_error(self, text: str) -> None:
        logger.error("오디오/Live 오류: %s", text)
        play_sound("error.mp3")

    async def _run_loop(self) -> None:
        await self.ws_bridge.start()
        self.audio.start()
        self.tts.speak("음성 비서가 시작되었습니다.", lang="ko")

        while self._running:
            try:
                result = await self.audio.get_utterance_transcript_async(
                    translation_mode=self.mode == AssistantMode.TRANSLATION_MODE
                )
                if result is None:
                    continue

                if result.translation_stop:
                    self.mode = AssistantMode.NORMAL_MODE
                    self.tts.speak("번역 모드를 종료합니다.", lang="ko")
                    continue

                if result.translation_start_lang:
                    self.mode = AssistantMode.TRANSLATION_MODE
                    self.translation_target_lang = result.translation_start_lang
                    self.tts.speak("번역 모드를 시작합니다.", lang="ko")
                    continue

                if self.mode == AssistantMode.TRANSLATION_MODE:
                    logger.info("AI(LLM) 번역 요청 시작 (target=%s)", self.translation_target_lang)
                    translated = self.llm.translate_text(result.text, self.translation_target_lang)
                    logger.info("AI(LLM) 번역 완료")
                    self.tts.speak(translated, lang=self.translation_target_lang)
                    continue

                # 게이트 통과한 발화는 모두 명령으로 처리 (OK 홍걸 웨이크 게이트 없음)
                logger.info("AI(LLM) 명령 분석 요청 시작")
                action = self.llm.plan_action(result.text)
                logger.info("AI(LLM) 명령 분석 완료: action=%s", action.get("action", "none"))
                await self._execute_action(action)
            except Exception as exc:
                logger.exception("런루프 예외: %s", exc)
                play_sound("error.mp3")

        self.audio.stop()
        await self.live_session.close()
        await self.ws_bridge.stop()

    async def _execute_action(self, action: dict) -> None:
        kind = action.get("action", "none")

        if kind == "move_edge_window":
            if self.windows is None:
                self.tts.speak("창 제어 기능이 비활성화되어 있습니다.", lang="ko")
                return
            target = action.get("target", "left")
            msg = self.windows.move_and_fullscreen(target)
            self.tts.speak(msg, lang="ko")
            return

        if kind == "youtube_control":
            payload = {
                "action": action.get("action_name") or action.get("youtube_action") or action.get("sub_action"),
                "query": action.get("query"),
                "seconds": action.get("seconds"),
            }
            if not payload["action"]:
                payload["action"] = "play"
            await self.ws_bridge.broadcast(payload)
            self.tts.speak("유튜브 명령을 전달했습니다.", lang="ko")
            return

        self.tts.speak(action.get("text", "명령을 이해하지 못했습니다."), lang="ko")

    def stop(self) -> None:
        self._running = False


def main() -> None:
    app = VoiceAssistantApp()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # add_signal_handler는 Unix 전용 (Windows 미지원)
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, app.stop)

    try:
        loop.run_until_complete(app._run_loop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
