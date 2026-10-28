from foliant.app import main

if __name__ == "__main__":     # un processo figlio (multiprocessing «spawn») non riavvia l'interfaccia
    raise SystemExit(main())
