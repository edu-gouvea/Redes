"""Protocolo de aplicação do Arena em Rede, compartilhado por servidor e cliente.

- mensagens:  tipos, codificação JSON e validação (o "formato" do protocolo)
- sequencia:  números de sequência e descarte de mensagens fora de ordem
- confiavel:  ACK, timeout adaptativo, retransmissão e duplicatas (só tipos confiáveis)
- transporte: a interface Transporte e o TransporteUDP (o único lugar com socket)
- sessao:     SessaoCliente, o lado cliente pronto para o robô e o cliente pygame

O contrato completo está em docs/PROTOCOLO.md.
"""
