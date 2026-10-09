/* Progressive enhancement: diagram -> local directory -> one detail pane. */
(()=>{
  const units=[...document.querySelectorAll('.diagram-unit')];
  function select(detail,{reveal=false,focus=false,hash=false}={}){
    const unit=detail?.closest('.diagram-unit'),explorer=unit?.querySelector('.detail-explorer');
    if(!explorer||!explorer.contains(detail))return;
    explorer.classList.add('is-enhanced');
    for(const item of explorer.querySelectorAll('.node-detail')){
      const active=item===detail;item.hidden=!active;item.open=active;item.classList.toggle('module-active',active);
    }
    for(const node of unit.querySelectorAll('.node')){
      if(node.dataset.node===detail.dataset.node)node.setAttribute('aria-current','step');else node.removeAttribute('aria-current');
    }
    for(const link of explorer.querySelectorAll('.node-directory a')){
      if(link.dataset.detail===detail.id)link.setAttribute('aria-current','step');else link.removeAttribute('aria-current');
    }
    const reader=detail.closest('.detail-reader');if(reader)reader.scrollTop=0;
    if(hash)history.replaceState(null,'','#'+detail.id);
    if(reveal)explorer.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
    if(focus)detail.focus({preventScroll:true});
  }
  for(const unit of units){
    const explorer=unit.querySelector('.detail-explorer');if(!explorer)continue;
    for(const node of unit.querySelectorAll('.node')){
      const activate=()=>select(document.getElementById('detail-'+node.dataset.node),{reveal:true,focus:true,hash:true});
      node.onclick=activate;node.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();activate()}};
    }
    const links=[...explorer.querySelectorAll('.node-directory a')];
    links.forEach((link,index)=>{
      link.onclick=event=>{event.preventDefault();select(document.getElementById(link.dataset.detail),{focus:true,hash:true})};
      link.onkeydown=event=>{
        if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
        event.preventDefault();const target=event.key==='Home'?0:event.key==='End'?links.length-1:(index+(event.key==='ArrowDown'?1:links.length-1))%links.length;
        select(document.getElementById(links[target].dataset.detail),{hash:true});links[target].focus({preventScroll:true});links[target].scrollIntoView({block:'nearest',inline:'nearest'});
      };
    });
    for(const summary of explorer.querySelectorAll('.node-detail>summary'))summary.onclick=event=>event.preventDefault();
    select(explorer.querySelector('.node-detail'));
  }
  function followHash(){const detail=document.getElementById(location.hash.slice(1));if(detail?.matches('.detail-explorer .node-detail'))select(detail,{reveal:true,focus:true})}
  followHash();addEventListener('hashchange',followHash);
  let printState=null;
  addEventListener('beforeprint',()=>{
    if(printState)return;
    printState=[...document.querySelectorAll('details')].map(detail=>({detail,open:detail.open,hidden:detail.hidden}));
    for(const {detail} of printState){detail.hidden=false;detail.open=true}
  });
  addEventListener('afterprint',()=>{
    if(!printState)return;
    for(const {detail,open,hidden} of printState){detail.open=open;detail.hidden=hidden}printState=null;
  });
})();
