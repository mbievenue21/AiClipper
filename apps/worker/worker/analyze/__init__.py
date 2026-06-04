"""Highlight analysis pipeline.

Stage 7 of the build. Reads transcript + audio + chat, scores candidate
clip windows, asks Gemini Flash to pick and title the best ones, and
returns ranked Highlight rows for persistence.

See ``pipeline.analyze_project`` for the entry point used by the
``analyze`` job handler.
"""

from .pipeline import AnalysisInput, AnalysisOutput, HighlightOut, analyze_project

__all__ = ["AnalysisInput", "AnalysisOutput", "HighlightOut", "analyze_project"]
