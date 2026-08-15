"""Migrazioni dell'hub anja — F-BackupDR Fase 3.

Ogni migrazione è un modulo `NNNN_descrizione.py` con una funzione:

    def up(hub_path: Path) -> None: ...

**Idempotente** (rieseguibile senza danni: controlla prima di modificare) perché il runner
può ri-tentare dopo un fallimento. Applicata UNA volta (il runner traccia gli id in
`<hub>/.anja-migrations.json`). Le migrazioni trasformano i DATI dell'hub (schema .db,
struttura dir, formato file) quando il codice nuovo lo richiede — mai il codice.
"""
