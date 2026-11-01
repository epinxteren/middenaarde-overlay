# Gedeelde audiocache uitrollen

Deze service deelt **audio**, geen afbeeldingen of OCR-tekstbestanden. De WAV-opname zelf bevat natuurlijk hoorbare verhaaltekst. Iedereen met de opname-URL kan luisteren. Downloads hebben geen login; uploads vereisen jouw eigen uploadsleutel. Geef die sleutel alleen aan vertrouwde gebruikers: zij kunnen nieuwe opnamen toevoegen. Provider-API-sleutels worden nooit naar deze server gestuurd.

## Op de server

Vereist: Docker met Compose, een domeinnaam die naar de server verwijst en poorten 80/443 bereikbaar. Er is geen cloudabonnement of Gemini-sleutel nodig voor de cache-server. Gebruik voor een publiek bereikbare installatie de meegeleverde HTTPS-proxy.

1. Clone deze repository en open `cache-server`.
2. Kopieer `.env.example` naar `.env`.
3. Stel `CACHE_DOMAIN` in op jouw domein, zonder `https://`.
4. Vervang `UPLOAD_TOKEN` door een willekeurig geheim van minstens 32 tekens. Genereer bijvoorbeeld met Python: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Deel dit geheim niet via GitHub.
5. Voer `docker compose up -d --build` uit. Caddy vraagt automatisch een TLS-certificaat aan.
6. Open `https://jouw-domein/health`: het antwoord hoort `ok` te zijn.

Op Windows PowerShell kun je stap 2 uitvoeren met `Copy-Item .env.example .env`. Op Linux met `cp .env.example .env`.

## In de Windows-app

Open **Audiocache**. **Gedeelde audiocache gebruiken en bijdragen** staat standaard aan.

- Vul `https://jouw-domein` in als serveradres.
- Vul de aparte **uploadsleutel** uit `.env` in en klik **Cache-instellingen opslaan**. De app bewaart deze sleutel versleuteld met Windows DPAPI.
- Zonder uploadsleutel kan de app bestaande opnamen downloaden, maar niets bijdragen.
- Zonder serveradres zijn er geen netwerkverzoeken naar de cache.
- Uitvinken stopt nieuwe cacheverzoeken en slaat wachtende uploads over. Een upload die al onderweg is kan nog afronden.

De app probeert de gedeelde opname vóór een nieuwe provider-aanvraag. Een onbereikbare cache blokkeert het voorlezen niet: na maximaal twee seconden valt de app terug op de provider, met een korte afkoelperiode voor nieuwe cachepogingen. Nieuwe audio wordt op de achtergrond geüpload. Een fout in de cache schakelt de spraakprovider niet uit. **Debuglogs** laat cachetreffers en uploadresultaten zien.

De cachesleutel is een SHA-256 van de tekst, provider, model, stem, taal en synthese-instellingen. Volume telt niet mee, omdat de app dat pas lokaal toepast. Wijziging van de tekst (ook een andere kindvriendelijke instructie die andere tekst oplevert) geeft een andere opname. Er worden geen API-sleutels, screenshots of losse oorspronkelijke teksten opgeslagen in metadata. Verschillende versies van provider-modellen kunnen dezelfde modelnaam gebruiken; wis zo nodig de betreffende servercache om opnieuw te genereren.

## Beheer

- Opnamen blijven bewaard in het Docker-volume `audio`; `docker compose down` bewaart dit volume.
- Standaard opslaglimiet: 5 GB (`CACHE_MAX_BYTES`). Bij een volle cache worden nieuwe uploads geweigerd; downloaden en providergebruik blijven werken.
- Maximaal 20 MB en vijf minuten per WAV, PCM 16-bit. Eerste upload per cachesleutel blijft behouden; bestaande bestanden worden niet overschreven.
- Maak backups van het `audio`-volume. Gebruik geen `docker compose down -v` tenzij je alle opgeslagen audio wilt verwijderen.
- Uploadsleutel roteren: wijzig `.env`, voer `docker compose up -d` uit en werk de sleutel bij in je apps.
- Houd `.env` privé. Alleen de HTTPS-proxy publiceert poorten; de interne Python-service hoort niet rechtstreeks op internet.
- De Compose-configuratie begrenst geheugen/CPU van de cache-service. Voor een grote publieke uitrol kun je bij je eigen proxy aanvullende verkeerslimieten instellen.

## Protocol en lokaal testen

- `GET /health` → `200 ok`
- `GET /v1/audio/<64 hex SHA-256>.wav` → WAV of `404`
- `PUT /v1/audio/<hash>.wav`, `Authorization: Bearer <UPLOAD_TOKEN>`, `Content-Type: audio/wav` → `201` nieuw, `200` bestond al
- `401` verkeerde sleutel; `413` te groot; `415` ongeldige WAV; `507` opslag vol.

De app volgt geen redirects met uploadsleutels. Gebruik het definitieve HTTPS-adres. HTTP wordt alleen voor `localhost`, `127.0.0.1` en `::1` geaccepteerd bij lokale tests.

De automatische tests starten een tijdelijke localhost-server en controleren upload, downloaden, authenticatie, WAV-validatie en cachesleutels. Docker/HTTPS moeten op jouw doelserver nog worden gecontroleerd; de infrastructuur wordt niet automatisch vanuit de app uitgerold.
