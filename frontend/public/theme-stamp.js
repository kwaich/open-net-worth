;(function () {
  var colors = { dark: '#0e1014', warm: '#edeae3', light: '#ffffff' }
  var p = 'dark'
  var pref = 'system'
  try {
    var v = localStorage.getItem('onw-theme')
    if (v === 'dark' || v === 'warm' || v === 'light') pref = v
  } catch (e) {
    /* A blocked preference store still follows the system. */
  }
  if (pref === 'system')
    p =
      typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: light)').matches
        ? 'light'
        : 'dark'
  else p = pref
  document.documentElement.setAttribute('data-theme', p)
  var m = document.querySelector('meta[name="theme-color"]')
  if (m) m.setAttribute('content', colors[p])
})()
