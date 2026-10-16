# Integração veicular: possibilidades e limitações

O DriveGuard AI é uma **ferramenta experimental de assistência ao motorista**, não um dispositivo automotivo certificado. Este documento descreve como a arquitetura permite evoluir para uso embarcado e o que precisa ser considerado antes disso.

> **Regra inegociável:** o sistema **não deve** ser conectado a freios, direção, acelerador, injeção ou qualquer sistema crítico do veículo. Sua única saída deve ser **alertar o motorista**.

## O que a arquitetura já oferece

- **Origem de vídeo substituível** — todo o sistema depende apenas do protocolo `FrameSource` (`app/frame_source.py`): `open()`, `read()`, `close()`, `is_open()`, `description` e `last_error`. Uma nova câmera exige somente uma nova classe que entregue quadros BGR (`numpy.ndarray`, `uint8`).
- **Análise independente da interface** — `DetectionEngine` e `DrowsinessAnalyzer` não importam Qt. Uma versão "headless" (sem tela, apenas alerta sonoro) pode reutilizá-los diretamente.
- **Tempo por timestamps** — o analisador funciona com qualquer taxa de quadros, o que facilita hardware mais lento.
- **Reconexão automática** e estados explícitos de falha (câmera indisponível, rosto ausente).

Exemplo de nova origem (esboço):

```python
class MinhaCameraVeicular:
    description = "Câmera veicular X"
    last_error = ""

    def open(self) -> bool: ...          # inicializa o SDK/driver
    def read(self): ...                  # retorna quadro BGR ou None
    def close(self) -> None: ...
    def is_open(self) -> bool: ...
```

Em seguida, basta passar uma fábrica dessa classe ao `MonitoringController` (parâmetro `source_factory`).

## Possibilidades de integração

| Opção | Viabilidade | Observações |
|---|---|---|
| **Câmeras USB (UVC)** | Alta | Funcionam como webcams via DirectShow/V4L2. Preferir modelos com bom desempenho em baixa luz. |
| **Câmeras veiculares compatíveis com captura por computador** | Depende do modelo | Só funcionam se expuserem vídeo como dispositivo UVC, stream RTSP ou SDK documentado. Dashcams que apenas gravam em cartão SD **não** servem. Cada modelo precisa ser testado. |
| **Câmeras infravermelhas (IR) com iluminador 850/940 nm** | Recomendada para uso noturno | Necessárias para enxergar o rosto no escuro sem ofuscar o motorista. O detector precisa ser avaliado com imagens IR (monocromáticas). |
| **Computadores embarcados** (mini PCs x86, Raspberry Pi 5, NVIDIA Jetson) | Média | Requer medir o FPS real; reduzir resolução e `process_fps`; considerar versão sem interface gráfica. |
| **Dispositivos de baixo consumo** | Baixa/média | Pode exigir modelos menores, aceleração (GPU/NPU) e taxa de análise reduzida. |
| **Sistemas de alerta auxiliares** | Alta | Saídas como buzzer USB, vibração no assento via dispositivo independente ou luz no painel auxiliar — sempre **isolados** dos sistemas críticos. |

## Limitações a considerar

### Alimentação elétrica
- A tensão do veículo varia (partida, alternador) e há picos transitórios: use fontes automotivas com proteção (conversores DC-DC adequados, fusível).
- O computador precisa de desligamento seguro ao desligar a ignição para não corromper o banco de dados e o sistema de arquivos.
- Notebooks na tomada 12 V podem limitar desempenho quando na bateria.

### Vibração e fixação
- Vibração desfoca a imagem e altera continuamente a pose aparente da cabeça; isso reduz a qualidade e pode afetar o sinal de inclinação.
- Use suportes rígidos, cabos com alívio de tensão e conectores travados.

### Iluminação
- **Noite:** câmeras RGB comuns não funcionam no escuro; o sistema indicará *Rosto não detectado*. É necessário IR.
- **Dia:** sol direto, sombras de árvores/postes passando rapidamente e contraluz causam variações bruscas de exposição.
- **Óculos:** reflexos de faróis e do painel nas lentes; óculos escuros bloqueiam a visão dos olhos (filtros IR de alguns óculos também).

### Campo de visão e posicionamento
- A câmera deve ver o rosto inteiro de frente (coluna de direção ou painel), sem ser obstruída pelo volante ou pelas mãos.
- Ângulos muito baixos (câmera no colo, olhando para cima) distorcem o EAR; a calibração ajuda, mas tem limites.
- Não pode obstruir a visão do motorista nem a abertura de airbags; respeite a legislação de trânsito local sobre dispositivos no para-brisa e no painel.
- Passageiros no quadro podem ser rastreados por engano.

### Desempenho computacional
- O pipeline mediu ~12–13 ms por quadro em um desktop Intel i5-9400F. Hardware embarcado pode ser várias vezes mais lento: meça antes de confiar.
- Temperatura dentro do carro estacionado ao sol pode exceder a faixa de operação de computadores comuns e causar *throttling*.

### Validação e responsabilidade
- Antes de qualquer uso real, seriam necessários testes controlados com voluntários, avaliação em bases de dados de sonolência e análise de falhas — nada disso foi feito neste projeto.
- Sistemas comerciais de monitoramento de motorista (DMS) passam por requisitos regulatórios e de certificação específicos; este projeto não atende a esses requisitos.
