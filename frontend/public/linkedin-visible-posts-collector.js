(() => {
  const cards = Array.from(
    document.querySelectorAll(
      ".feed-shared-update-v2, .occludable-update, article, [data-urn*='activity']"
    )
  );

  const posts = cards
    .map((card) => {
      const text =
        card.innerText
          ?.split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
          .join(" ") || "";
      const link = card.querySelector("a[href*='/posts/'], a[href*='activity-']");
      const time = card.querySelector("time");
      const source = card.querySelector("span[dir='ltr'], .update-components-actor__name");

      return {
        source_name: source?.innerText?.trim() || "LinkedIn",
        source_kind: "company",
        posted_at: time?.getAttribute("datetime") || time?.innerText?.trim() || "",
        text,
        reactions: 0,
        comments: 0,
        post_url: link?.href || location.href,
      };
    })
    .filter((post) => post.text.length > 40);

  const output = JSON.stringify(posts, null, 2);
  navigator.clipboard
    ?.writeText(output)
    .then(() => alert(`Copied ${posts.length} visible post(s) as JSON.`))
    .catch(() => {
      console.log(output);
      alert("Could not copy automatically. JSON was printed to the browser console.");
    });
})();
