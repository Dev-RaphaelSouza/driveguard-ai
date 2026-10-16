# Procedimento de avaliação manual

Os testes automatizados verificam a **lógica** do DriveGuard AI com dados sintéticos. Este roteiro verifica o **comportamento real** com uma pessoa diante da câmera e ajuda a calibrar os parâmetros para o seu equipamento.

> **Segurança:** realize todos os cenários **sentado à mesa ou com o veículo estacionado e desligado**. Nunca simule sonolência nem feche os olhos dirigindo. Os testes servem para avaliar o software, não para autorizar seu uso em direção real.

## Preparação

1. Execute `python main.py --check` e confirme que todos os itens estão `OK`.
2. Posicione a câmera à altura dos olhos ou levemente abaixo, a 50–80 cm do rosto, com o rosto inteiro no quadro.
3. Use iluminação frontal difusa (evite janela atrás de você).
4. Inicie o monitoramento e mantenha os olhos abertos olhando para a câmera até a mensagem **Calibração concluída**.
5. Anote as condições: câmera, resolução, FPS exibido no selo AO VIVO, iluminação, uso de óculos.
6. Para resultados comparáveis, registre cada cenário em vídeo **somente com autorização da pessoa filmada** e reproduza-o depois com `python main.py --video arquivo.mp4`.

## Como registrar

Para cada cenário, anote em uma planilha:

| Campo | Exemplo |
|---|---|
| Cenário / repetição | C3 / 2 |
| Condições | webcam USB 720p, luz de teto, sem óculos |
| Estado esperado | SONOLÊNCIA PROVÁVEL em ~1,5 s |
| Estado observado | SONOLÊNCIA PROVÁVEL em ~1,6 s |
| Alarme sonoro? | sim |
| Falso positivo / falso negativo | — |
| Observações | EAR fechado ≈ 0,10; limiar 0,18 |

Repita cada cenário **pelo menos 5 vezes**. Ao final, consulte a aba **Histórico** e, se quiser, exporte o CSV para análise.

## Cenários

| # | Cenário | Como executar | Resultado esperado |
|---|---|---|---|
| C1 | Olhos abertos normalmente | 2 min olhando para a câmera, piscando naturalmente | **NORMAL** o tempo todo; piscadas ≈ 10–25/min; PERCLOS baixo; nenhum alerta |
| C2 | Piscadas normais | 30 piscadas rápidas espaçadas em 1 min | Nenhum alerta; tile "Fechamento atual" não passa de ~0,4 s |
| C3 | Fechamento ocular prolongado | Fechar os olhos por 1 s, 2 s e 3 s (5× cada) | 1 s: sem alerta. 2 s e 3 s: **SONOLÊNCIA PROVÁVEL** perto de 1,5 s, alarme e evento "Fechamento ocular prolongado" no histórico |
| C4 | Recuperação | Após C3, manter os olhos abertos | O estado volta a NORMAL/ATENÇÃO somente após ~5 s sem sinais |
| C5 | Piscadas longas repetidas | 4 fechamentos de ~0,8 s em 1 min | **ATENÇÃO** ("piscadas longas recentes") |
| C6 | Bocejos | 3 bocejos reais ou simulados (boca bem aberta ≥ 2 s) em 5 min | Eventos "Bocejo"; **ATENÇÃO** após o 3º; nunca SONOLÊNCIA apenas por bocejos |
| C7 | Fala/canto | Falar ou cantar por 1 min | Idealmente nenhum bocejo registrado. Se houver, aumentar o limiar de MAR |
| C8 | Rosto parcialmente oculto | Cobrir um olho com a mão; depois a boca | Qualidade cai ("olhos inconsistentes"); sem alarme de sonolência; possível ROSTO NÃO DETECTADO |
| C9 | Rosto fora da câmera | Sair do quadro por 2 s e por 8 s | 2 s: ROSTO NÃO DETECTADO e retorno. 8 s: aviso violeta e som de falha de monitoramento após ~5 s |
| C10 | Mudança de iluminação | Apagar/acender a luz principal; luz lateral forte | Qualidade reflete a mudança ("pouca iluminação"); sem alarmes falsos durante a transição |
| C11 | Óculos de grau | Repetir C1 e C3 com óculos; inclinar a cabeça para provocar reflexo | Anotar diferenças de EAR e qualidade; se necessário, recalibrar com os óculos |
| C12 | Óculos escuros | Repetir C1 | Espera-se qualidade baixa ou falha: o sistema não deve indicar NORMAL com olhos invisíveis por longos períodos |
| C13 | Movimentos da cabeça | Olhar para os lados (retrovisores) por 1–2 s; abaixar a cabeça por 4 s | Olhar para os lados: sem alertas. Cabeça abaixada: ATENÇÃO ("cabeça inclinada") |
| C14 | Câmera desconectada | Desconectar a câmera USB durante o monitoramento e reconectar | **CÂMERA INDISPONÍVEL** com aviso e som de falha; reconexão automática em alguns segundos |
| C15 | Câmera ocupada | Abrir a câmera em outro aplicativo e iniciar o monitoramento | Mensagem clara de câmera indisponível/ocupada, sem travar a interface |
| C16 | Silêncio temporário | Durante um alerta, clicar em **Silenciar** | Som interrompido, banner continua, monitoramento segue; o som volta após o tempo configurado |

## Métricas sugeridas

- **Taxa de detecção** em C3 (≥ 2 s): alertas corretos ÷ tentativas.
- **Falsos positivos por hora** em C1/C2/C7: alertas de sonolência sem fechamento prolongado ÷ horas observadas.
- **Latência**: tempo entre o início do fechamento e o alarme (compare com o parâmetro configurado).
- **Disponibilidade**: fração do tempo fora de ROSTO NÃO DETECTADO em uso normal.

## Ajuste iterativo

1. Comece pelos padrões.
2. Muitos falsos positivos em C1/C2 → reduza a sensibilidade ou recalibre.
3. Falhas em C3 → aumente a sensibilidade e verifique o EAR com olhos fechados no tile **EAR**.
4. Altere **um parâmetro por vez** e repita os cenários afetados.
5. Registre a configuração final (a pasta de dados contém `settings.json`).

## Limitações desta avaliação

Simular sonolência com olhos fechados voluntariamente **não equivale** à sonolência real, que envolve fechamentos parciais, movimentos lentos das pálpebras e variações individuais. Resultados bons neste roteiro **não validam** o sistema para segurança veicular.
