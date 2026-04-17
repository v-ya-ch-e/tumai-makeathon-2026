(function () {
  const form = document.getElementById("hunt-form");
  if (!form) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());

    const requirements = {
      city: data.city,
      max_rent_eur: Number(data.max_rent_eur),
      min_rent_eur: Number(data.min_rent_eur || 0),
      min_size_m2: Number(data.min_size_m2 || 10),
      max_size_m2: Number(data.max_size_m2 || 40),
      min_wg_size: Number(data.min_wg_size || 2),
      max_wg_size: Number(data.max_wg_size || 8),
      preferred_districts: (data.preferred_districts || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      avoid_districts: [],
      languages: ["Deutsch", "Englisch"],
      notes: data.notes || "",
      max_listings_to_consider: Number(data.max_listings_to_consider || 20),
      max_messages_to_send: Number(data.max_messages_to_send || 5),
    };

    const credentials = {
      username: data.wg_username,
      password: data.wg_password,
      storage_state_path: data.storage_state_path || null,
    };

    const profile = {
      first_name: data.first_name,
      last_name: data.last_name || "",
      age: Number(data.age),
      email: data.email,
      phone: data.phone || "",
      occupation: data.occupation || "Student",
      bio: data.bio || "",
    };

    const payload = {
      requirements,
      credentials,
      profile,
      dry_run: form.dry_run.checked,
      headless: form.headless.checked,
    };

    const button = form.querySelector("button[type='submit']");
    button.disabled = true;
    button.textContent = "Starting…";

    try {
      const res = await fetch("/wg/hunt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text}`);
      }
      const run = await res.json();
      window.location.href = `/wg/runs/${run.id}`;
    } catch (err) {
      button.disabled = false;
      button.textContent = "Start autonomous hunt";
      alert("Could not start hunt: " + err.message);
    }
  });
})();
