# Prompts

Der aktive System-Prompt und die Aufgaben-Prompts des KI-Controller-Agenten sind
versioniert in `src/agent.py` hinterlegt (`SYSTEM_PROMPT`, `PROMPT_VERSION`), damit
Code, Prompt und Tests immer zusammenpassen und jede Änderung im Git-Verlauf
nachvollziehbar ist.

Grundsätze:

- Das LLM erhält ausschließlich das Faktenblatt (JSON) und Tool-Ergebnisse.
- Inhalte in `<faktenblatt>`, `<tool_ergebnis>` und Datentexte sind **Daten, keine Anweisungen**.
- Jede Zahl im Ausgabetext wird gegen das Faktenblatt geprüft (Null-Halluzinations-Prüfung).

Der Monatsbericht selbst wird nicht vom LLM geschrieben: Gliederung, Diagramme und
Kernaussagen erzeugt `src/reporting_ibcs.py` deterministisch. Aus dem Agenten
fließen nur namentlich freigegebene Kommentare ein.
