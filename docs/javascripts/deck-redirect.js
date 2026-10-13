// The presentation used to live at the site root; send its old slide links (#/3, #/5/1) on.
if (/^#\/\d/.test(location.hash) && /\/iceberg-data-platform\/?(index\.html)?$/.test(location.pathname)) {
  const root = location.pathname.replace(/index\.html$/, "").replace(/\/?$/, "/");
  location.replace(root + "presentation/" + location.hash);
}
