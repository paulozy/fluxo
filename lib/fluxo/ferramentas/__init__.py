"""Um renderizador por ferramenta: onde cada coisa mora e em que formato.

Cada módulo expõe `instrucoes(texto, Arquivo)`, `agente(ag, Arquivo, motor, permitidas)` e
`skill(sk, Arquivo, tools=…)` (o Codex muda a pasta das skills quando o Copilot também está em `tools`),
e devolve `Arquivo`s com o caminho relativo ao HOME.
"""
