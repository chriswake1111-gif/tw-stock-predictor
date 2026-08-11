export function PlaceholderPage({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <section className="page page--placeholder" aria-labelledby="placeholder-title">
      <h1 id="placeholder-title">{title}</h1>
      <p>{description}</p>
    </section>
  );
}
