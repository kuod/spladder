import sys
import os
import scipy as sp
import pickle
import h5py

if __name__ == "__main__":
    __package__ = "modules.alt_splice"

### local imports
from .verify import *
from .write import *
from ..rproc import rproc, rproc_wait
from ..helpers import compute_psi

def _prepare_count_hdf5(CFG, OUT, events, event_features, sample_idx=None):
    
    ### load gene info
    if 'spladder_infile' in CFG and os.path.exists(CFG['spladder_infile']):
        (genes, inserted) = pickle.load(open(CFG['spladder_infile']), 'rb')
    else:
        prune_tag = ''
        if CFG['do_prune']:
            prune_tag = '_pruned'
        validate_tag = ''
        if CFG['validate_splicegraphs']:
            validate_tag = '.validated'
        if not sample_idx is None:
            (genes, inserted) = pickle.load(open('%s/spladder/genes_graph_conf%i.%s%s%s.pickle' % (CFG['out_dirname'], CFG['confidence_level'], CFG['samples'][sample_idx], validate_tag, prune_tag)), 'rb')
        else:
            (genes, inserted) = pickle.load(open('%s/spladder/genes_graph_conf%i.%s%s%s.pickle' % (CFG['out_dirname'], CFG['confidence_level'], CFG['merge_strategy'], validate_tag, prune_tag), 'rb'))

    ### write strain and gene indices to hdf5
    OUT.create_dataset(name='strains', data=[val.encode('utf8').strip() for val in CFG['strains']])
    feat = OUT.create_group(name='event_features')
    for f in event_features:
       feat.create_dataset(name=f, data=sp.array([x.encode('utf8').strip() for x in event_features[f]]))
    OUT.create_dataset(name='gene_names', data=sp.array([x.name.encode('utf8').strip() for x in genes]))
    OUT.create_dataset(name='gene_chr', data=sp.array([x.chr.encode('utf8').strip() for x in genes]))
    OUT.create_dataset(name='gene_strand', data=sp.array([x.strand.encode('utf8').strip() for x in genes]))
    OUT.create_dataset(name='gene_pos', data=sp.array([[x.start, x.stop] for x in genes], dtype='int'))


def analyze_events(CFG, event_type, sample_idx=None):

    if CFG['rproc'] and not os.path.exists('%s/event_count_chunks' % CFG['out_dirname']):
        os.makedirs('%s/event_count_chunks' % CFG['out_dirname'])

    for replicate in CFG['replicate_idxs']:
        
        print('confidence %i / replicate %i' % (CFG['confidence_level'], replicate))

        if len(CFG['replicate_idxs']) > 1:
            rep_tag = '_R%i' % r_idx
        else:
            rep_tag = ''

        if CFG['merge_strategy'] == 'single':
            fn_out = '%s/%s_%s%s_C%i.pickle' % (CFG['out_dirname'], CFG['samples'][sample_idx], event_type, rep_tag, CFG['confidence_level'])
        else:
            fn_out = '%s/%s_%s%s_C%i.pickle' % (CFG['out_dirname'], CFG['merge_strategy'], event_type, rep_tag, CFG['confidence_level'])
        fn_out_conf = fn_out.replace('.pickle', '.confirmed.pickle')
        fn_out_count = fn_out.replace('.pickle', '.counts.hdf5')

        ### define result files
        fn_out_txt = fn_out.replace('.pickle', '.txt')
        fn_out_struc = fn_out.replace('.pickle', '.struc.txt')
        fn_out_conf_txt = fn_out_conf.replace('.pickle', '.txt')
        fn_out_conf_bed = fn_out_conf.replace('.pickle', '.bed')
        fn_out_conf_struc = fn_out_conf.replace('.pickle', '.struc.txt')
        fn_out_conf_tcga = fn_out_conf.replace('.pickle', '.tcga.txt')
        fn_out_conf_icgc = fn_out_conf.replace('.pickle', '.icgc.txt.gz')
        fn_out_conf_gff3 = fn_out_conf.replace('.pickle', '.gff3')

        ### check if there is anything to do
        if os.path.exists(fn_out_txt) and os.path.exists(fn_out_conf_txt) and os.path.exists(fn_out_conf_tcga) and os.path.exists(fn_out_conf_icgc) and os.path.exists(fn_out_conf_gff3):
            print('All output files for %s exist.\n' % event_type)
            continue

        event_features = {'mult_exon_skip': ['valid', 'exon_pre_cov', 'exons_cov', 'exon_aft_cov', 'exon_pre_exon_conf', 'exon_exon_aft_conf', 'exon_pre_exon_aft_conf', 'sum_inner_exon_conf', 'num_inner_exon', 'len_inner_exon'],
                          'intron_retention': ['valid', 'intron_cov', 'exon1_cov', 'exon2_cov', 'intron_conf', 'intron_cov_region'],
                          'exon_skip': ['valid', 'exon_cov', 'exon_pre_cov', 'exon_aft_cov', 'exon_pre_exon_conf', 'exon_exon_aft_conf', 'exon_pre_exon_aft_conf'],
                          'mutex_exons': ['valid', 'exon_pre_cov', 'exon1_cov', 'exon2_cov', 'exon_aft_cov', 'exon_pre_exon1_conf', 'exon_pre_exon2_conf', 'exon1_exon_aft_conf', 'exon2_exon_aft_conf'],
                          'alt_3prime': ['valid', 'exon_diff_cov', 'exon_const_cov', 'intron1_conf', 'intron2_conf'],
                          'alt_5prime': ['valid', 'exon_diff_cov', 'exon_const_cov', 'intron1_conf', 'intron2_conf']}

        ### check, if confirmed version exists
        if not os.path.exists(fn_out_count):

            events_all_ = pickle.load(open(fn_out, 'rb'))
            if isinstance(events_all_, tuple):
                events_all = events_all_[0]
                events_all_strains = events_all_[1]
            else:
                events_all = events_all_
                events_all_strains = None

            ### DEBUG!!!
            #for xx in xrange(events_all.shape[0]):
            #    events_all[xx].verified = []

            ### add strain information, so we can do two way chunking!
            if events_all_strains is None:
                events_all_strains = CFG['strains']
                
            ### handle case where we did not find any event of this type
            if sp.sum([x.event_type == event_type for x in events_all]) == 0:
                OUT = h5py.File(fn_out_count, 'w')
                OUT.create_dataset(name='event_counts', data=[0])
                _prepare_count_hdf5(CFG, OUT, events_all, event_features, sample_idx=sample_idx)
                OUT.close()
                confirmed_idx = sp.array([], dtype='int')
            else:
                if not CFG['rproc']:
                    #events_all = verify_all_events(events_all, range(len(CFG['strains'])), CFG['bam_fnames'][replicate, :], event_type, CFG)
                    # TODO handle replicate setting
                    (events_all, counts) = verify_all_events(events_all, list(range(len(CFG['strains']))), CFG['bam_fnames'], event_type, CFG)

                    psi = sp.empty((counts.shape[0], counts.shape[2]), dtype='float')
                    for i in range(counts.shape[2]):
                        psi[:, i] = compute_psi(counts[:, :, i], event_type, CFG) 

                    OUT = h5py.File(fn_out_count, 'w')
                    OUT.create_dataset(name='event_counts', data=counts, compression='gzip')
                    OUT.create_dataset(name='psi', data=psi, compression='gzip')
                    OUT.create_dataset(name='gene_idx', data=sp.array([x.gene_idx for x in events_all], dtype='int'), compression='gzip')
                    _prepare_count_hdf5(CFG, OUT, events_all, event_features, sample_idx=sample_idx)
                else:
                    jobinfo = []
                    PAR = dict()
                    chunk_size_events = 1000
                    chunk_size_strains = 500
                    for i in range(0, events_all.shape[0], chunk_size_events):
                        idx_events = sp.arange(i, min(i + chunk_size_events, events_all.shape[0]))
                        for j in range(0, len(CFG['strains']), chunk_size_strains):
                            idx_strains = sp.arange(j, min(j + chunk_size_strains, len(CFG['strains'])))
                            PAR['ev'] = events_all[idx_events].copy()
                            PAR['strain_idx'] = idx_strains
                            #PAR['list_bam'] = CFG['bam_fnames'][replicate, :]
                            # TODO handle replicate setting
                            PAR['list_bam'] = CFG['bam_fnames']
                            PAR['out_fn'] = '%s/event_count_chunks/%s_%i_%i_R%i_C%i.pickle' % (CFG['out_dirname'], event_type, i, j, replicate, CFG['confidence_level'])
                            PAR['event_type'] = event_type
                            PAR['CFG'] = CFG
                            if os.path.exists(PAR['out_fn']):
                                print('Chunk event %i, strain %i already completed' % (i, j))
                            else:
                                print('Submitting job %i, event chunk %i, strain chunk %i' % (len(jobinfo) + 1, i, j))
                                jobinfo.append(rproc('verify_all_events', PAR, 30000, CFG['options_rproc'], 60 * 5))
                    
                    rproc_wait(jobinfo, 20, 1.0, 1)
                    
                    events_all_ = []
                    gene_idx_ = []
                    print('Collecting results from chunks ...')
                    OUT = h5py.File(fn_out_count, 'w')
                    for i in range(0, events_all.shape[0], chunk_size_events):
                        idx_events = sp.arange(i, min(i + chunk_size_events, events_all.shape[0]))
                        for j in range(0, len(CFG['strains']), chunk_size_strains):
                            idx_strains = sp.arange(j, min(j + chunk_size_strains, len(CFG['strains'])))
                            print('\r%i (%i), %i (%i)' % (i, events_all.shape[0], j, len(CFG['strains'])))
                            out_fn = '%s/event_count_chunks/%s_%i_%i_R%i_C%i.pickle' % (CFG['out_dirname'], event_type, i, j, replicate, CFG['confidence_level'])
                            if not os.path.exists(out_fn):
                                print('ERROR: not finished %s' % out_fn, file=sys.stderr)
                                sys.exit(1)
                            ev_, counts_ = pickle.load(open(out_fn, 'rb'))
                            if j == 0:
                                ev = ev_
                                counts = counts_
                            else:
                                counts = sp.r_[counts, counts_]
                                for jj in range(len(ev_)):
                                    ev[jj].verified = sp.r_[ev[jj].verified, ev_[jj].verified]
                                    
                        psi = sp.empty((counts.shape[0], counts.shape[2]), dtype='float')
                        for j in range(counts.shape[2]):
                            psi[:, j] = compute_psi(counts[:, :, j], event_type, CFG) 

                        if i == 0:
                            OUT.create_dataset(name='event_counts', data=counts, maxshape=(len(CFG['strains']), len(event_features[event_type]), None), compression='gzip')
                            OUT.create_dataset(name='psi', data=sp.atleast_2d(psi), maxshape=(psi.shape[0], None), compression='gzip')
                        else:
                            tmp = OUT['event_counts'].shape
                            OUT['event_counts'].resize((tmp[0], tmp[1], tmp[2] + len(ev)))
                            OUT['event_counts'][:, :, tmp[2]:] = counts
                            tmp = OUT['psi'].shape
                            OUT['psi'].resize((tmp[0], tmp[1] + len(ev)))
                            OUT['psi'][:, tmp[1]:] = psi
                        events_all_ = sp.r_[events_all_, ev]
                        gene_idx_ = sp.r_[gene_idx_, [x.gene_idx for x in ev]]

                    assert(events_all.shape[0] == events_all_.shape[0])
                    assert(sp.all([sp.all(events_all[e].exons1 == events_all_[e].exons1) for e in range(events_all.shape[0])]))
                    OUT.create_dataset(name='gene_idx', data=gene_idx_)
                    events_all = events_all_
                    _prepare_count_hdf5(CFG, OUT, events_all, event_features, sample_idx=sample_idx)
                
                ### write more event infos to hdf5
                if event_type == 'exon_skip':
                    event_pos = sp.array([x.exons2.ravel() for x in events_all])
                elif event_type == 'intron_retention':
                    event_pos = sp.array([x.exons2.ravel() for x in events_all])
                elif event_type in ['alt_3prime', 'alt_5prime']:
                    event_pos = sp.array([unique_rows(sp.c_[x.exons1, x.exons2]).ravel() for x in events_all])
                elif event_type == 'mult_exon_skip':
                    event_pos = sp.array([x.exons2[[0, 1, -2, -1], :].ravel() for x in events_all])
                elif event_type == 'mutex_exons':
                    event_pos = sp.array([sp.c_[x.exons1[0, :], x.exons1[1, :], x.exons2[1, :], x.exons2[2, :]] for x in events_all])

                OUT.create_dataset(name='event_pos', data=event_pos)

                for i in range(events_all.shape[0]):
                    events_all[i].num_verified = sp.sum(events_all[i].verified, axis=0)
                    events_all[i].confirmed = sp.array(events_all[i].num_verified).min()
                
                num_verified = sp.array([x.num_verified for x in events_all])

                #verified_count = []
                #for min_verified = 1:length(CFG.strains),
                #    verified_count(min_verified) = sum([events_all.confirmed] >= min_verified) ;
                
                confirmed_idx = sp.where([x.confirmed >= 1 for x in events_all])[0]
                
                if confirmed_idx.shape[0] > 0:
                    OUT.create_dataset(name='conf_idx', data=confirmed_idx)
                OUT.create_dataset(name='verified', data=num_verified)

                ### close HDF5
                OUT.close()
    
            ### make verified matrix bool
            for ev in events_all:
                ev.verified = ev.verified.astype('bool')
    
            ### save events
            pickle.dump((events_all, events_all_strains), open(fn_out, 'wb'), -1)
            pickle.dump(confirmed_idx, open(fn_out_conf, 'wb'), -1)

        else:
            print('\nLoading event data from %s' % fn_out)
            (events_all, events_all_strains) = pickle.load(open(fn_out, 'rb'))
            confirmed_idx = pickle.load(open(fn_out_conf, 'rb'))

        if events_all.shape[0] == 0:
            print('\nNo %s event could be found. - Nothing to report' % event_type)
            continue
        else:
            print('\nReporting complete %s events:' % event_type)

        if CFG['output_txt']:
            if os.path.exists(fn_out_txt):
                print('%s already exists' % fn_out_txt)
            else:
                write_events_txt(fn_out_txt, events_all, fn_out_count)

        if CFG['output_struc']:
            if os.path.exists(fn_out_struc):
                print('%s already exists' % fn_out_struc)
            else:
                write_events_structured(fn_out_struc, events_all, fn_out_count)

        if confirmed_idx.shape[0] == 0:
            print('\nNo %s event could be confirmed. - Nothing to report.' % event_type)
            continue
        else:
            print('\nReporting confirmed %s events:' % event_type)

        if CFG['output_confirmed_gff3']:
            if os.path.exists(fn_out_conf_gff3):
                print('%s already exists' % fn_out_conf_gff3)
            else:
                write_events_gff3(fn_out_conf_gff3, events_all, confirmed_idx)

        if CFG['output_confirmed_txt']:
            if os.path.exists(fn_out_conf_txt):
                print('%s already exists' % fn_out_conf_txt)
            else:
                write_events_txt(fn_out_conf_txt, CFG['strains'], events_all, fn_out_count, event_idx=confirmed_idx)

        if CFG['output_confirmed_bed']:
            if os.path.exists(fn_out_conf_bed):
                print('%s already exists' % fn_out_conf_bed)
            else:
                write_events_bed(fn_out_conf_bed, events_all, idx=confirmed_idx)

        if CFG['output_confirmed_struc']:
            if os.path.exists(fn_out_conf_struc):
                print('%s already exists' % fn_out_conf_struc)
            else:
                write_events_structured(fn_out_conf_struc, events_all, fn_out_count, confirmed_idx)

        if CFG['output_confirmed_tcga']:
            if os.path.exists(fn_out_conf_tcga):
                print('%s already exists' % fn_out_conf_tcga)
            else:
                write_events_tcga(fn_out_conf_tcga, CFG['strains'], events_all, fn_out_count, event_idx=confirmed_idx)

        if CFG['output_confirmed_icgc']:
            if os.path.exists(fn_out_conf_icgc):
                print('%s already exists' % fn_out_conf_icgc)
            else:
                write_events_icgc(fn_out_conf_icgc, CFG['strains'], events_all, fn_out_count, event_idx=confirmed_idx)


        if CFG['output_filtered_txt']:
            fn_out_conf_txt = fn_out_conf.replace('.pickle', '.filt0.05.txt')
            if os.path.exists(fn_out_conf_txt):
                print('%s already exists' % fn_out_conf_txt)
            else:
                print('\nWriting filtered events (sample freq 0.05):')
                cf_idx = sp.where([x.confirmed for x in events_all[confirmed_idx]] >= (0.05 * CFG['strains'].shape[0]))[0]
                write_events_txt(fn_out_conf_txt, CFG['strains'], events_all, fn_out_count, event_idx=confirmed_idx[cf_idx])

            fn_out_conf_txt = fn_out_conf.replace('.pickle', '.filt0.1.txt')
            if os.path.exists(fn_out_conf_txt):
                print('%s already exists' %  fn_out_conf_txt)
            else:
                print('\nWriting filtered events (sample freq 0.01):')
                cf_idx = sp.where([x.confirmed for x in events_all[confirmed_idx]] >= (0.01 * CFG['strains'].shape[0]))[0]
                write_events_txt(fn_out_conf_txt, CFG['strains'], events_all, fn_out_count, event_idx=confirmed_idx[cf_idx])
