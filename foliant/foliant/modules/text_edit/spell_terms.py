"""Parole del mestiere aggiunte al dizionario italiano incluso (orologeria e compravendita).

Il dizionario di pyspellchecker non conosce molti termini tecnici (e qualche forma comune, come
«bracciale» o «revisione»): senza questo elenco una perizia o un annuncio risultano pieni di
parole sottolineate. Ogni voce ha singolare e plurale (e il femminile, dove serve); le parole
straniere sono solo quelle d'uso corrente nel settore.
"""

from __future__ import annotations

_IT_WATCH = """
acciaio acciai
anse ansa
automatico automatici automatica automatiche
bariletto bariletti
bilanciere bilancieri
bracciale bracciali braccialetto braccialetti
calibro calibri
carica cariche
carrure
cassa casse cassetta cassette
certificato certificati
chiusura chiusure
cinturino cinturini
complicazione complicazioni
corona corone
cronografo cronografi cronografico cronografica cronografiche cronografici
cronometro cronometri cronometrico cronometrica cronometrici cronometriche
datario datari
deployante deployanti
fasatura fasature
fibbia fibbie
fondello fondelli
garanzia garanzie
ghiera ghiere
impermeabilità impermeabile impermeabili
indice indici
lancetta lancette
lubrificazione lubrificazioni
lucidatura lucidature
luminescente luminescenti luminescenza
lunetta lunette
maglia maglie
matricola matricole
meccanico meccanici meccanica meccaniche
movimento movimenti
patina patine
perizia perizie
pulsante pulsanti
quadrante quadranti
referenza referenze
rehaut
revisione revisioni revisionato revisionata revisionati revisionate
riserva riserve
rodio rodiato rodiata rodiati rodiate
rotore rotori
satinatura satinature satinato satinata satinati satinate
scatola scatole
scheletrato scheletrata scheletrati scheletrate
serigrafia serigrafie
subacqueo subacquea subacquei subacquee
tachimetro tachimetri tachimetrico tachimetrica
titanio
trizio
zaffiro zaffiri
rattrapante rattrapanti
tourbillon
vintage
"""

# frequenza data alle parole aggiunte: abbastanza alta da comparire tra i suggerimenti
TERM_FREQUENCY = 1000

IT_WATCH_TERMS: frozenset[str] = frozenset(_IT_WATCH.split())

EXTRA_WORDS: dict[str, frozenset[str]] = {"it": IT_WATCH_TERMS}
