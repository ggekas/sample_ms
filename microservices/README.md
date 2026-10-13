# Items API starter

A small FastAPI + MySQL + Redis stack. MySQL and Redis are reachable only inside the Compose network; the API is published on port 8000. The API supports item CRUD, `/health`, and a Redis read-through cache for list and detail reads.

## Run on Ubuntu with Docker Compose

From this folder on the VM:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Replace all three placeholder passwords with different, long random values. Keep `.env` on the VM and do not send it to a registry or commit it.

Build and start the services:

```bash
docker compose up -d --build
docker compose ps
```

Check the API from the VM:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/items
curl -fsS http://localhost:8000/docs
```

Create an item:

```bash
curl -fsS -X POST http://localhost:8000/items \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo item","description":"first test","price":"12.50"}'
```

List again to check the cache:

```bash
curl -fsS http://localhost:8000/items
```

Use the returned item ID for `GET /items/{id}`, `PUT /items/{id}`, and `DELETE /items/{id}`. To inspect logs:

```bash
docker compose logs --tail=100 api mysql redis
```

To stop the app without deleting its database/cache data:

```bash
docker compose down
```

Do not use `docker compose down -v` unless you intend to delete the stored data.

## Copy from Windows to the EC2 VM

Run these commands in local Git Bash from the parent folder that contains `microservices/`. They package only the app folder, not other workspace files:

```bash
tar --force-local -czf microservices.tar.gz microservices
scp -i "/c/path/to/gpukeys.pem" microservices.tar.gz ubuntu@YOUR_EC2_PUBLIC_IP:~/microservices.tar.gz
```

Then, in the SSH session on Ubuntu:

```bash
mkdir -p ~/microservices
tar -xzf ~/microservices.tar.gz -C ~
cd ~/microservices
cp .env.example .env
chmod 600 .env
nano .env
docker compose up -d --build
```

Change the key path and public IP to your own. Keep the `.pem` file private. The default EC2 security group may block inbound TCP 8000; test with `curl` on the VM first. Only add an inbound rule after approval, and restrict it to the intended source. Never expose ports 3306 or 6379.

## Notes

- The MySQL initialization SQL seeds two example rows on the first start of an empty `mysql-data` volume. Replacing the SQL file does not update an existing volume.
- This is a compact starter, not the complete seven-prompt production workflow. It has no automated test suite or TLS configuration.
- Image names are set to `kharatramesh/hpe` to match the prompt pack. This Compose file builds locally and does not push images.
